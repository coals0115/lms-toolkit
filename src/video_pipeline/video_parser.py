import asyncio
from playwright.sync_api import Page


async def on_request(event, shared_state: dict):
    url = event["request"]["url"]
    print(f"[CDP] 요청 감지: {url}")
    if shared_state["video_url"] is None and url.endswith(".mp4"):
        if "commons.ssu.ac.kr" in url or "commonscdn.com" in url:
            # intro.mp4 같은 플레이어 UI 파일 제외
            if "intro.mp4" not in url and "media_files" in url:
                print(f"[CDP] 동영상 파일 감지: {url}")
                shared_state["video_url"] = url


async def register_cdp_video_sniffer(page: Page, shared_state: dict):
    """CDP 세션을 생성하고 네트워크 요청을 감시. CDP 클라이언트를 반환하여 나중에 정리할 수 있도록 함."""
    client = await page.context.new_cdp_session(page)
    await client.send("Network.enable")
    client.on("Network.requestWillBeSent",
              lambda e: asyncio.create_task(on_request(e, shared_state)))
    return client  # CDP 클라이언트 반환


async def find_canvas_video_frame(page: Page, shared_state: dict):
    for _ in range(10):
        outer = page.frame(name="tool_content")
        if outer:
            print(f"[DEBUG] outer iframe 찾음 - URL: {outer.url}")
            try:
                title_element = await outer.wait_for_selector(".xnlailct-title", timeout=5000)
                if title_element:
                    shared_state["title"] = await title_element.text_content()
                    print(f"[DEBUG] 제목 찾음: {shared_state['title']}")
            except:
                print("[WARN] 제목을 찾을 수 없음")
            for frame in page.frames:
                if frame.parent_frame == outer and "commons.ssu.ac.kr" in frame.url:
                    print(f"[DEBUG] inner iframe 찾음 - URL: {frame.url}")
                    return frame
        await asyncio.sleep(1)
    print("[ERROR] iframe 탐색 실패")
    return None


async def trigger_video_play(frame):
    try:
        play_btn = await frame.wait_for_selector(".vc-front-screen-play-btn", timeout=5000)
        await play_btn.click()
        print("[INFO] 재생 버튼 클릭됨.")
    except:
        print("[WARN] 재생 버튼을 찾을 수 없음.")
        return

    try:
        confirm_dialog = await frame.wait_for_selector("#confirm-dialog", timeout=2000)
        cancel_btn = await confirm_dialog.query_selector(".confirm-cancel-btn.confirm-btn")
        if cancel_btn:
            await cancel_btn.click()
            print("[INFO] 확인창 닫음.")
    except:
        pass


async def extract_video_url(page: Page) -> tuple[str, str]:
    shared_state = {"video_url": None, "title": None}
    cdp_client = await register_cdp_video_sniffer(page, shared_state)

    try:
        video_frame = await find_canvas_video_frame(page, shared_state)
        if not video_frame:
            return None, None

        await trigger_video_play(video_frame)

        print("[DEBUG] 비디오 URL 대기 시작")
        for _ in range(100):  # 최대 10초 대기 (0.1초 간격)
            if shared_state["video_url"]:
                print(f"[DEBUG] 비디오 URL 찾음: {shared_state['video_url']}")
                return shared_state["video_url"], shared_state["title"]
            await asyncio.sleep(0.1)

        print("[DEBUG] 비디오 URL을 찾지 못했습니다.")
        return None, None
    finally:
        # CDP 세션 정리 - 네트워크 모니터링 비활성화
        try:
            await cdp_client.send("Network.disable")
            await cdp_client.detach()
            print("[DEBUG] CDP 세션 정리 완료")
        except Exception as e:
            print(f"[WARN] CDP 세션 정리 중 오류 (무시): {e}")
