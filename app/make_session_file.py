import asyncio
import nodriver as uc
from dotenv import load_dotenv
import os
# 環境変数の読み込み
load_dotenv()

import time

async def wait_for_element(tab, selector, timeout=30, check_interval=0.5):
    """
    ページ上に要素が表示されるまで待機します。
    
    引数:
        tab: ブラウザタブオブジェクト
        selector: 要素のCSSセレクタ
        timeout: 待機する最大時間（秒）
        check_interval: 要素をチェックする間隔（秒）
        
    戻り値:
        見つかった場合は要素オブジェクト、タイムアウトした場合はNone
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        element = await tab.query_selector(selector)
        if element:
            return element
        await asyncio.sleep(check_interval)
    return None

async def wait_for_find(tab, str, timeout=30, check_interval=0.5):
    """
    ページ上に要素が表示されるまで待機します。
    
    引数:
        tab: ブラウザタブオブジェクト
        str: 検出文字列
        timeout: 待機する最大時間（秒）
        check_interval: 要素をチェックする間隔（秒）
        
    戻り値:
        見つかった場合は要素オブジェクト、タイムアウトした場合はNone
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        element = await tab.find(str, best_match=True)
        if element:
            return element
        await asyncio.sleep(check_interval)
    return None
async def main():
    browser = await uc.start()
    page = await browser.get('https://chatgpt.com/')
    await page.sleep(10)

    print("等待 Log in 按钮...")
    login_btn = await wait_for_find(page, 'Log in')
    if login_btn:
        print("找到 Log in 按钮，点击...")
        await login_btn.click()
    else:
        print("未找到 Log in 按钮")
        browser.stop()
        return
    await page.sleep(5)
    print("等待邮箱输入框...")
    # 页面可能已跳转，等待新页面加载
    await page
    # 尝试多个可能的选择器
    for selector in ['input[id="email-input"]', 'input[type="email"]', 'input[name="username"]']:
        try:
            mail = await wait_for_element(page, selector, timeout=15)
            if mail:
                break
        except Exception:
            continue

    if mail:
        print("找到邮箱输入框，输入邮箱...")
        await mail.send_keys(os.getenv('MAIL'))
    else:
        print("未找到邮箱输入框，请通过 VNC 查看页面状态")
        print("等待 60 秒以便手动检查...")
        await page.sleep(60)
        browser.stop()
        return
    await page.sleep(5)
    print("等待 Continue 按钮...")
    send = await wait_for_find(page,'Continue', timeout=60)
    if send:
        print("找到 Continue 按钮，点击...")
        await send.click()
    else:
        print("未找到 Continue 按钮")
        browser.stop()
        return
    await page.sleep(5)
    print("等待密码输入框...")
    await page
    password = None
    for selector in ['input[id="password"]', 'input[type="password"]', 'input[name="password"]']:
        try:
            password = await wait_for_element(page, selector, timeout=15)
            if password:
                print(f"  通过选择器 '{selector}' 找到密码框")
                break
        except Exception:
            continue

    if password:
        print("输入密码...")
        await password.send_keys(os.getenv('PASSWORD'))
    else:
        print("未找到密码输入框，请通过 VNC 查看页面状态")
        print("等待 60 秒以便手动检查...")
        await page.sleep(60)
        browser.stop()
        return
    await page
    print("等待第二个 Continue 按钮...")
    send = await wait_for_find(page,'Continue', timeout=60)
    if send:
        print("找到 Continue 按钮，点击...")
        await send.click()
    else:
        print("未找到第二个 Continue 按钮")
        browser.stop()
        return
    await page.sleep(10)
    print("等待登录完成...")
    search = await wait_for_find(page,'Search', timeout=60)
    if search:
        print("登录成功！")
    else:
        print("登录可能未完成")
    await page.sleep(2)
    print("保存会话...")
    await browser.cookies.save()
    print("会话文件已保存！")
    browser.stop()

    


if __name__ == '__main__':
    # since asyncio.run never worked (for me)
    uc.loop().run_until_complete(main())
