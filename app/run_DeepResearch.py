import nodriver as uc
import asyncio
import subprocess
import re
import yaml
import argparse
import json
import glob
import shutil
import os
from pathlib import Path
from nodriver.cdp.input_ import dispatch_key_event
from nodriver.cdp import input_ as cdp_input
from nodriver.cdp import page as cdp_page
from nodriver.cdp import runtime as cdp_runtime
from nodriver.cdp import target as cdp_target

def sanitize_path(path_str):
    invalid_chars = r'<>:"/\\|?*'
    for char in invalid_chars:
        path_str = path_str.replace(char, '_')
    path_str = path_str.strip().strip('.')
    if not path_str:
        path_str = "unnamed_output"
    return path_str

def setup_output_directory(config, prompt_path, output_dir):
    prompt_path = Path(prompt_path)
    prompt_filename = sanitize_path(prompt_path.stem)
    if output_dir is None:
        output_dir = Path(config['output']['base_dir']) / prompt_filename
    else:
        output_dir = Path(output_dir) / prompt_filename
    output_dir = Path(str(output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / sanitize_path(config['output']['html_file'])
    md_path = output_dir / sanitize_path(config['output']['markdown_file'])
    return output_dir, html_path, md_path

async def send_text_with_newlines(tab, textarea, text, is_shift=True):
    lines = text.split('\n')
    for i, line in enumerate(lines):
        if line:
            await textarea.send_keys(line)
        if i < len(lines) - 1:
            if is_shift:
                await tab.send(dispatch_key_event(type_='keyDown', modifiers=8, windows_virtual_key_code=16, key="Shift", code="ShiftLeft"))
                await tab.send(dispatch_key_event(type_='keyDown', modifiers=8, windows_virtual_key_code=13, key="Enter", code="Enter"))
                await tab.send(dispatch_key_event(type_='keyUp', modifiers=8, windows_virtual_key_code=13, key="Enter", code="Enter"))
                await tab.send(dispatch_key_event(type_='keyUp', modifiers=0, windows_virtual_key_code=16, key="Shift", code="ShiftLeft"))
            else:
                await tab.send(dispatch_key_event(type_='keyDown', modifiers=8, windows_virtual_key_code=13, key="Enter", code="Enter"))
                await tab.send(dispatch_key_event(type_='keyUp', modifiers=8, windows_virtual_key_code=13, key="Enter", code="Enter"))


async def main(config_path="config.yaml", prompt_path=None, output_dir=None):
    config_path = Path(config_path)
    with config_path.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if prompt_path is None:
        prompt_path = Path(config['prompt']['default_path'])
    else:
        prompt_path = Path(prompt_path)

    if not prompt_path.exists():
        print(f"⚠️ 提示文件 '{prompt_path}' 不存在")
        return

    output_dir, html_path, md_path = setup_output_directory(config, prompt_path, output_dir)
    print(f"📂 输出目录: {output_dir}")

    with prompt_path.open("r", encoding=config['prompt']['encoding']) as f:
        prompt_text = f.read()

    # 清空下载目录
    download_dir = "/root/Downloads"
    Path(download_dir).mkdir(parents=True, exist_ok=True)
    for f in glob.glob(f"{download_dir}/*.md"):
        os.remove(f)

    # 启动浏览器
    browser = await uc.start(headless=config['browser']['headless'])
    await browser.cookies.load()

    tab = await browser.get(config['urls']['chatgpt'])
    # 全屏浏览器窗口
    await tab.maximize()
    await tab.sleep(config['timings']['initial_wait'])

    # 1. 点击侧边栏 Deep Research 进入模式
    print("🔍 切换到 Deep Research 模式...")
    deep_research_button = await tab.find(config['buttons']['deep_research'], best_match=True)
    if deep_research_button:
        await deep_research_button.click()
        await tab.sleep(5)
        print("✅ 已切换到 Deep Research 模式")
    else:
        print("⚠️ 未找到 Deep Research 按钮")
        await browser.cookies.save()
        browser.stop()
        return

    # 2. 查找输入框
    await tab.sleep(3)
    elem = None
    for selector in [config['selectors']['text_input_placeholder'], 'p[data-placeholder="Get a detailed report"]', 'div#prompt-textarea']:
        try:
            elem = await tab.select(selector)
            if elem:
                print(f"✅ 找到输入框: {selector}")
                break
        except Exception:
            continue
    if not elem:
        print("⚠️ 未找到输入框")
        await browser.cookies.save()
        browser.stop()
        return
    await elem.update()

    # 查找父容器
    while elem.parent:
        elem = elem.parent
        if elem is None:
            break
        await elem.update()
        if config['selectors']['parent_container'] in elem.attributes:
            break
    if elem:
        await elem.update()
        container = elem
        await container.update()

    # 3. 输入提示文本并发送
    textarea = await container.query_selector('textarea')
    await send_text_with_newlines(tab, textarea, prompt_text)
    await tab.sleep(2)

    send_button = await container.query_selector(config['selectors']['send_button'])
    if not send_button:
        send_button = await tab.query_selector(config['selectors']['send_button'])
    if not send_button:
        try:
            send_button = await tab.query_selector('button[aria-label="Send"]')
        except Exception:
            pass
    if send_button:
        await send_button.click()
        print("📤 提示已发送")
    else:
        print("⚠️ 未找到发送按钮")
        await browser.cookies.save()
        browser.stop()
        return
    await tab.sleep(config['timings']['initial_wait'])

    # 4. 等待 Deep Research 完成
    print("⏳ 等待 Deep Research 完成...")
    await wait_for_deep_research(tab, config)

    # 5. 保存 URL
    current_url = await tab.evaluate('window.location.href')
    url_str = str(current_url)
    url_txt_path = Path(output_dir) / "url.txt"
    with url_txt_path.open("w", encoding="utf-8") as f:
        f.write(url_str)
    print(f"💾 URL 已保存: {url_txt_path}")

    # 6. 等待 iframe 内容完全加载
    print("📥 等待 iframe 内容加载...")
    await tab.sleep(10)

    # 7. 通过点击 iframe 内下载按钮获取 Markdown
    print("📥 正在下载研究报告...")
    downloaded = await download_from_iframe(tab, config, md_path)

    # 验证下载内容是否有效
    if downloaded and not is_valid_markdown(md_path):
        print("⚠️ 下载的 Markdown 内容无效或过短，尝试其他方式...")
        downloaded = False

    if not downloaded:
        print("📥 尝试通过 CDP 直接从 iframe 提取内容...")
        downloaded = await extract_markdown_from_iframe(tab, config, md_path)

    if not downloaded:
        print("⚠️ CDP 提取失败，尝试剪贴板方式...")
        await fallback_copy_result(tab, config, md_path)

    # 7. 保存 HTML
    try:
        articles = await tab.select_all(config['selectors']['main_article'])
        if articles:
            last_article = articles[-1]
            html_content = await last_article.get_html()
            with html_path.open("w", encoding="utf-8") as f:
                f.write(html_content)
            print(f"💾 HTML 已保存: {html_path}")
    except Exception as e:
        print(f"⚠️ HTML 保存失败: {e}")

    # 保存 cookie 并退出
    await browser.cookies.save()
    browser.stop()
    print("✅ 完成！")


async def check_iframe_research_completed(tab):
    """检查 iframe 内是否出现 'Research completed' 文本"""
    try:
        result = await tab.evaluate('''
            (() => {
                let iframes = document.querySelectorAll('iframe[title="internal://deep-research"]');
                if (iframes.length === 0) return false;
                // 检查页面上是否有 Research completed 文本
                let body = document.body ? document.body.innerText : '';
                return body.includes('Research completed');
            })()
        ''')
        return result == True
    except Exception:
        return False


async def wait_for_deep_research(tab, config):
    """等待 Deep Research 完成（通过检测 iframe 内 'Research completed' 或输入框/语音按钮重新出现）"""
    max_wait = int(config['timings']['max_wait_time'] / config['timings']['button_check_interval'])
    elapsed = 0
    # Deep Research 至少需要几分钟
    print("  等待中（至少 60 秒）...")
    await tab.sleep(60)

    while elapsed < max_wait:
        try:
            await tab
            # 检测方式1: iframe 内出现 "Research completed" 文本
            if await check_iframe_research_completed(tab):
                await tab.sleep(5)
                print("✅ Deep Research 已完成 (检测到 'Research completed')")
                return True

            # 检测方式2: speech 按钮或 send 按钮重新出现
            speech_button = await tab.query_selector(config['selectors']['speech_button'])
            send_button = await tab.query_selector(config['selectors']['send_button'])
            if speech_button is not None or send_button is not None:
                await tab.sleep(10)
                print("✅ Deep Research 已完成 (检测到按钮)")
                return True

            # 检测方式3: 最后一个 assistant turn 中有 copy 按钮
            # （用户 turn 也可能有 copy 按钮，所以必须检查最后一个 turn 是 assistant 的）
            completed = await tab.evaluate('''
                (() => {
                    var turns = document.querySelectorAll('[data-testid^="conversation-turn-"]');
                    if (turns.length < 2) return false;
                    var lastTurn = turns[turns.length - 1];
                    // assistant turn 包含 class="agent-turn" 的元素
                    var isAssistant = lastTurn.querySelector('.agent-turn') !== null;
                    var hasCopy = lastTurn.querySelector('[data-testid="copy-turn-action-button"]') !== null;
                    return isAssistant && hasCopy;
                })()
            ''')
            if completed == True:
                await tab.sleep(5)
                print("✅ Deep Research 已完成 (检测到 assistant turn copy 按钮)")
                return True

            # 检测方式4: iframe 高度 > 100（内容已渲染）
            iframe_ready = await tab.evaluate('''
                (() => {
                    var iframes = document.querySelectorAll('iframe[title="internal://deep-research"]');
                    if (iframes.length === 0) return false;
                    var iframe = iframes[iframes.length - 1];
                    var h = iframe.getBoundingClientRect().height;
                    // 高度 > 100 且页面上有 "Research completed" 文本
                    var text = document.body ? document.body.innerText : '';
                    return h > 100 && text.includes('Research completed');
                })()
            ''')
            if iframe_ready == True:
                await tab.sleep(5)
                print("✅ Deep Research 已完成 (检测到 iframe 已渲染)")
                return True
        except Exception as e:
            print(f"⚠️ 检测错误: {e}")

        await tab.sleep(config['timings']['button_check_interval'])
        elapsed += 1
        # 每 60 次循环打印一次进度
        if elapsed % 60 == 0:
            minutes = int(elapsed * config['timings']['button_check_interval'] / 60) + 1
            print(f"  已等待约 {minutes} 分钟...")

    # 超时后 reload 重试
    print("⏳ 首次等待超时，刷新页面重试...")
    await tab.reload()
    await tab.sleep(15)
    elapsed = 0
    while elapsed < max_wait:
        try:
            await tab
            if await check_iframe_research_completed(tab):
                await tab.sleep(5)
                print("✅ Deep Research 已完成 (检测到 'Research completed')")
                return True

            speech_button = await tab.query_selector(config['selectors']['speech_button'])
            send_button = await tab.query_selector(config['selectors']['send_button'])
            if speech_button is not None or send_button is not None:
                await tab.sleep(10)
                print("✅ Deep Research 已完成 (检测到按钮)")
                return True

            completed = await tab.evaluate('''
                (() => {
                    var turns = document.querySelectorAll('[data-testid^="conversation-turn-"]');
                    if (turns.length < 2) return false;
                    var lastTurn = turns[turns.length - 1];
                    var isAssistant = lastTurn.querySelector('.agent-turn') !== null;
                    var hasCopy = lastTurn.querySelector('[data-testid="copy-turn-action-button"]') !== null;
                    return isAssistant && hasCopy;
                })()
            ''')
            if completed == True:
                await tab.sleep(5)
                print("✅ Deep Research 已完成 (检测到 assistant turn copy 按钮)")
                return True
        except Exception as e:
            print(f"⚠️ 检测错误: {e}")
            break
        await tab.sleep(config['timings']['button_check_interval'])
        elapsed += 1

    print("⌛ 超时：Deep Research 未在预期时间内完成")
    return False


def is_valid_markdown(md_path, min_length=200):
    """检查保存的 Markdown 文件内容是否有效（非空且足够长）"""
    try:
        if not Path(md_path).exists():
            return False
        content = Path(md_path).read_text(encoding='utf-8').strip()
        if len(content) < min_length:
            print(f"⚠️ Markdown 内容过短 ({len(content)} 字符)，可能不完整")
            return False
        return True
    except Exception:
        return False


async def _send_to_iframe_session(tab, session_id, method, params=None):
    """通过 tab 的 websocket 发送带 sessionId 的 CDP 命令到 iframe session"""
    ws = tab._websocket
    the_id = next(tab.__count__)
    message = {"id": the_id, "method": method, "sessionId": str(session_id)}
    if params:
        message["params"] = params
    fut = asyncio.get_event_loop().create_future()
    class _FakeTx:
        def __init__(s): s.id = the_id; s._f = fut
        def __call__(s, **r):
            if "error" in r: s._f.set_exception(Exception(str(r["error"])))
            else: s._f.set_result(r.get("result", {}))
    tab.mapper[the_id] = _FakeTx()
    await ws.send(json.dumps(message))
    try:
        return await asyncio.wait_for(fut, timeout=15)
    except asyncio.TimeoutError:
        tab.mapper.pop(the_id, None)
        return None


async def extract_markdown_from_iframe(tab, config, md_path):
    """通过 CDP session 进入嵌套 iframe 提取内容并点击 Export 按钮"""
    try:
        # 1. 找到外层 deep-research iframe target
        targets = await tab.send(cdp_target.get_targets())
        outer_tid = None
        for t in targets:
            if t.type_ == 'iframe' and 'deep_research' in (t.url or ''):
                outer_tid = t.target_id
                break
        if not outer_tid:
            print("⚠️ CDP: 未找到 deep-research iframe target")
            return False

        # 2. attach 到外层 iframe
        outer_sid = await tab.send(cdp_target.attach_to_target(outer_tid, flatten=True))
        print(f"  CDP: 已 attach 到外层 iframe, session={outer_sid}")

        # 3. 获取外层 iframe 的 frame tree，找到内层 iframe#root
        ft = await _send_to_iframe_session(tab, outer_sid, "Page.getFrameTree")
        if not ft or 'frameTree' not in ft:
            print(f"⚠️ CDP: 获取 iframe frame tree 失败, ft={ft}")
            return False

        inner_frame_id = None
        for cf in ft['frameTree'].get('childFrames', []):
            inner_frame_id = cf['frame']['id']
            print(f"  CDP: 找到内层 frame: {inner_frame_id}")
            break
        if not inner_frame_id:
            print("⚠️ CDP: 未找到内层 iframe")
            return False

        # 4. 在外层 session 中对内层 frame 创建 isolated world
        ctx_result = await _send_to_iframe_session(tab, outer_sid, "Page.createIsolatedWorld", {
            "frameId": inner_frame_id, "worldName": "extract_content"
        })
        if not ctx_result:
            print("⚠️ CDP: 创建 isolated world 失败")
            return False
        ctx_id = ctx_result.get('executionContextId')
        print(f"  CDP: 内层 frame 执行上下文: {ctx_id}")

        # 5. 先尝试点击 Export 按钮下载原始 Markdown
        download_dir = "/root/Downloads"
        Path(download_dir).mkdir(parents=True, exist_ok=True)
        for f in glob.glob(f"{download_dir}/*.md"):
            os.remove(f)

        click_result = await _send_to_iframe_session(tab, outer_sid, "Runtime.evaluate", {
            "expression": '''
                (() => {
                    var buttons = document.querySelectorAll('button');
                    for (var b of buttons) {
                        var label = (b.getAttribute('aria-label') || '').toLowerCase();
                        if (label.includes('export') || label.includes('download')) {
                            b.click();
                            return "clicked: " + b.getAttribute('aria-label');
                        }
                    }
                    return "not found";
                })()
            ''',
            "contextId": ctx_id
        })
        if click_result:
            click_val = click_result.get('result', {}).get('value', '')
            print(f"  CDP: Export 点击结果: {click_val}")
            if 'clicked' in str(click_val):
                print(f"📥 已点击 iframe 内 Export 按钮: {click_val}")
                await tab.sleep(5)
                files = glob.glob(f"{download_dir}/*.md")
                if files:
                    shutil.copy2(files[0], str(md_path))
                    if is_valid_markdown(md_path):
                        print(f"💾 Markdown 已保存 (Export 按钮): {md_path}")
                        return True

        # 6. Export 按钮失败，直接提取内容作为备选
        text_result = await _send_to_iframe_session(tab, outer_sid, "Runtime.evaluate", {
            "expression": '''
                (() => {
                    // 优先提取 article/main/prose 等内容容器
                    var containers = document.querySelectorAll('article, main, [role="main"], .markdown-body, .prose');
                    for (var c of containers) {
                        if (c.innerText && c.innerText.length > 200) return c.innerText;
                    }
                    // 备选：提取 body 但跳过开头的 UI 噪音
                    var text = document.body ? document.body.innerText : '';
                    // 去掉 "Research completed..." 之前的行
                    var idx = text.indexOf('\\n\\n');
                    if (idx > 0 && idx < 200) {
                        var afterHeader = text.substring(idx).trim();
                        if (afterHeader.length > 200) return afterHeader;
                    }
                    return text;
                })()
            ''',
            "contextId": ctx_id
        })
        if text_result:
            content = text_result.get('result', {}).get('value', '')
            print(f"  CDP: innerText 长度: {len(content) if content else 0}")
            if content and len(content.strip()) > 200:
                with open(str(md_path), 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"💾 Markdown 已保存 (CDP innerText): {md_path}")
                return True

        print("⚠️ CDP: iframe 内未提取到有效内容")
        return False

    except Exception as e:
        print(f"⚠️ CDP iframe 提取失败: {e}")
        return False


async def download_from_iframe(tab, config, md_path):
    """通过 CDP 坐标点击 iframe 内的下载按钮来获取 Markdown 文件"""
    download_dir = "/root/Downloads"
    # 清空旧文件
    for f in glob.glob(f"{download_dir}/*.md"):
        os.remove(f)

    # 滚动到 iframe 并获取位置
    await tab.evaluate('''
        var iframes = document.querySelectorAll('iframe[title="internal://deep-research"]');
        if (iframes.length > 0) iframes[iframes.length - 1].scrollIntoView();
    ''')
    await tab.sleep(2)

    iframe_info = await tab.evaluate('''
        JSON.stringify((() => {
            var iframes = document.querySelectorAll('iframe[title="internal://deep-research"]');
            if (iframes.length === 0) return null;
            var iframe = iframes[iframes.length - 1];
            var rect = iframe.getBoundingClientRect();
            return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
        })())
    ''')

    if not iframe_info or iframe_info == 'null':
        print("⚠️ 未找到 Deep Research iframe")
        return False

    info = json.loads(iframe_info)
    ix, iy, iw, ih = info['x'], info['y'], info['width'], info['height']
    print(f"📐 iframe 位置: x={ix}, y={iy}, w={iw}, h={ih}")

    # 先鼠标悬停到 iframe 中心，触发 hover 使下载按钮显示
    await tab.send(cdp_input.dispatch_mouse_event(type_='mouseMoved', x=ix + iw / 2, y=iy + ih / 2))
    await tab.sleep(1)
    # 移动到 iframe 顶部区域
    await tab.send(cdp_input.dispatch_mouse_event(type_='mouseMoved', x=ix + iw - 40, y=iy + 5))
    await tab.sleep(1)

    # 下载按钮在 iframe 最顶端，沿顶部从右到左扫描
    test_positions = [
        (ix + iw - 30, iy + 5, "顶端右-1"),
        (ix + iw - 50, iy + 5, "顶端右-2"),
        (ix + iw - 30, iy + 12, "顶端右偏下-1"),
        (ix + iw - 50, iy + 12, "顶端右偏下-2"),
        (ix + iw - 70, iy + 5, "顶端右-3"),
        (ix + iw - 70, iy + 12, "顶端右偏下-3"),
        (ix + iw - 30, iy + 20, "顶端右偏下-4"),
        (ix + iw - 50, iy + 20, "顶端右偏下-5"),
        (ix + iw - 90, iy + 5, "顶端右-4"),
        (ix + iw - 90, iy + 12, "顶端右偏下-6"),
    ]

    for click_x, click_y, desc in test_positions:
        await tab.send(cdp_input.dispatch_mouse_event(type_='mouseMoved', x=click_x, y=click_y))
        await tab.sleep(0.5)
        await tab.send(cdp_input.dispatch_mouse_event(
            type_='mousePressed', x=click_x, y=click_y,
            button=cdp_input.MouseButton('left'), click_count=1
        ))
        await tab.send(cdp_input.dispatch_mouse_event(
            type_='mouseReleased', x=click_x, y=click_y,
            button=cdp_input.MouseButton('left'), click_count=1
        ))
        await tab.sleep(3)

        # 检查下载目录
        files = glob.glob(f"{download_dir}/*.md")
        if files:
            print(f"✅ 下载成功 ({desc})")
            # 复制到输出目录
            shutil.copy2(files[0], str(md_path))
            print(f"💾 Markdown 已保存: {md_path}")
            return True

    # 最终检查
    await tab.sleep(5)
    files = glob.glob(f"{download_dir}/*.md")
    if files:
        shutil.copy2(files[0], str(md_path))
        print(f"💾 Markdown 已保存: {md_path}")
        return True

    return False


async def fallback_copy_result(tab, config, md_path):
    """回退方案：通过复制按钮 + 剪贴板获取结果"""
    try:
        articles = await tab.select_all(config['selectors']['main_article'])
        if not articles:
            return False
        last_article = articles[-1]
        await last_article.scroll_into_view()
        await tab.sleep(2)
        await last_article.mouse_move()
        await last_article.focus()
        await tab.sleep(1)

        copy_buttons = await tab.select_all(config['selectors']['copy_button'])
        if copy_buttons:
            await copy_buttons[-1].mouse_click()
            await tab.sleep(2)
            custom_command = config['clipboard']['command'].replace('output.md', str(md_path))
            subprocess.run(custom_command, shell=True)
            print(f"💾 Markdown 已保存 (剪贴板): {md_path}")
            return True
    except Exception as e:
        print(f"⚠️ 剪贴板方式失败: {e}")
    return False


def parse_arguments():
    parser = argparse.ArgumentParser(description='ChatGPT Deep Research 自动化脚本')
    parser.add_argument('--config', type=str, default='config.yaml',
                        help='配置文件路径 (默认: config.yaml)')
    parser.add_argument('--prompt_path', type=str, default=None,
                        help='提示文件路径')
    parser.add_argument('--output_dir', type=str, default=None,
                        help='输出目录路径')
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_arguments()
    uc.loop().run_until_complete(main(config_path=args.config, prompt_path=args.prompt_path, output_dir=args.output_dir))
