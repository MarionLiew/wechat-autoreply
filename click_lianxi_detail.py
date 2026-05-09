#!/usr/bin/env python3
"""
点击 经营线索 聊天中的"线索详情>>"链接，并扫描是否出现"去联系"按钮
"""
import time
import atomacos
import Quartz
from config import settings

def get_app():
    for app in atomacos.NativeUIElement.getRunningApps():
        if app.bundleIdentifier() == settings.wecom_bundle_id:
            return atomacos.getAppRefByBundleId(settings.wecom_bundle_id)
    return None

def get_pid():
    import subprocess
    result = subprocess.run(
        ["pgrep", "-f", "WeCom"],
        capture_output=True, text=True
    )
    pids = result.stdout.strip().split("\n")
    return int(pids[0]) if pids and pids[0] else None

def postclick(pid, x, y):
    pt = Quartz.CGPointMake(x, y)
    for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        ev = Quartz.CGEventCreateMouseEvent(None, etype, pt, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPostToPid(pid, ev)
        time.sleep(0.05)

def switch_to_jingyingxiansuo(app):
    """Switch to 经营线索 chat via AXSelected"""
    try:
        nav = app.AXMainWindow.findFirst(AXRole="AXList")
    except Exception:
        nav = None

    if nav is None:
        # try finding all lists
        try:
            lists = app.AXMainWindow.findAll(AXRole="AXList")
            print(f"Found {len(lists)} AXList elements")
            nav = lists[0] if lists else None
        except Exception as e:
            print(f"Error finding nav: {e}")
            return False

    if nav is None:
        print("No AXList found")
        return False

    try:
        rows = nav.AXChildren
        print(f"Nav has {len(rows)} children")
        for row in rows:
            try:
                cells = row.AXChildren
                for cell in cells:
                    try:
                        txt = str(getattr(cell, "AXValue", "") or "")
                        name = str(getattr(cell, "AXDescription", "") or "")
                        if "经营线索" in txt or "经营线索" in name:
                            print(f"Found 经营线索 row: txt={txt[:30]}, name={name[:30]}")
                            row.AXSelected = True
                            time.sleep(1.0)
                            return True
                    except Exception:
                        pass
                # also check direct text
                txt = str(getattr(row, "AXValue", "") or "")
                name = str(getattr(row, "AXDescription", "") or "")
                label = str(getattr(row, "AXLabel", "") or "")
                if "经营线索" in txt or "经营线索" in name or "经营线索" in label:
                    print(f"Found 经营线索 direct row")
                    row.AXSelected = True
                    time.sleep(1.0)
                    return True
            except Exception:
                pass
    except Exception as e:
        print(f"Error iterating nav: {e}")
    return False

def find_linxiangqing_links(app):
    """Find all 线索详情>> AXLink elements in the chat area"""
    links = []
    try:
        win = app.AXMainWindow
        # Find scroll area with chat (width > 800)
        scroll_areas = win.findAll(AXRole="AXScrollArea")
        chat_scroll = None
        for sa in scroll_areas:
            sz = getattr(sa, "AXSize", None)
            if sz and sz[0] > 800:
                chat_scroll = sa
                print(f"Chat scroll: pos={getattr(sa,'AXPosition',None)} sz={sz}")
                break

        if chat_scroll is None:
            print("No chat scroll area found")
            return links

        # Traverse: scroll → table → rows → cells → textareas → links
        try:
            tables = chat_scroll.AXChildren
            for t in tables:
                if getattr(t, "AXRole", "") != "AXTable":
                    continue
                rows = t.AXChildren
                print(f"Table has {len(rows)} rows")
                for row in rows:
                    if getattr(row, "AXRole", "") != "AXRow":
                        continue
                    try:
                        cells = row.AXChildren
                        for cell in cells:
                            try:
                                cell_children = cell.AXChildren
                                for child in cell_children:
                                    role = getattr(child, "AXRole", "")
                                    if role == "AXTextArea":
                                        try:
                                            ta_children = child.AXChildren
                                            for tc in ta_children:
                                                tc_role = getattr(tc, "AXRole", "")
                                                tc_title = str(getattr(tc, "AXTitle", "") or "")
                                                if tc_role == "AXLink" and "线索详情" in tc_title:
                                                    pos = getattr(tc, "AXPosition", None)
                                                    sz2 = getattr(tc, "AXSize", None)
                                                    print(f"Found 线索详情 link: pos={pos} sz={sz2}")
                                                    links.append(tc)
                                        except Exception as e:
                                            pass  # AXErrorFailure expected in some rows
                            except Exception:
                                pass
                    except Exception:
                        pass
        except Exception as e:
            print(f"Error traversing table: {e}")
    except Exception as e:
        print(f"Error in find_linxiangqing_links: {e}")
    return links

def scan_for_qulianxi(app):
    """Scan the whole app for 去联系 elements"""
    results = []
    try:
        win = app.AXMainWindow
        # Check all buttons
        buttons = win.findAll(AXRole="AXButton")
        for b in buttons:
            title = str(getattr(b, "AXTitle", "") or "")
            desc = str(getattr(b, "AXDescription", "") or "")
            if "去联系" in title or "去联系" in desc:
                pos = getattr(b, "AXPosition", None)
                print(f"AXButton 去联系: pos={pos}")
                results.append(("AXButton", b, pos))

        # Check all links
        all_links = win.findAll(AXRole="AXLink")
        for lk in all_links:
            title = str(getattr(lk, "AXTitle", "") or "")
            desc = str(getattr(lk, "AXDescription", "") or "")
            val = str(getattr(lk, "AXValue", "") or "")
            if "去联系" in title or "去联系" in desc or "去联系" in val:
                pos = getattr(lk, "AXPosition", None)
                print(f"AXLink 去联系: pos={pos}")
                results.append(("AXLink", lk, pos))

        # Check static texts
        texts = win.findAll(AXRole="AXStaticText")
        for t in texts:
            val = str(getattr(t, "AXValue", "") or "")
            if "去联系" in val:
                pos = getattr(t, "AXPosition", None)
                print(f"AXStaticText 去联系: pos={pos}")
                results.append(("AXStaticText", t, pos))
    except Exception as e:
        print(f"Error scanning for 去联系: {e}")
    return results

def main():
    app = get_app()
    if app is None:
        print("WeCom not found")
        return

    pid = get_pid()
    print(f"WeCom PID: {pid}")

    # Step 1: Switch to 经营线索 chat
    print("\n=== 切换到经营线索 ===")
    switched = switch_to_jingyingxiansuo(app)
    if not switched:
        print("Failed to switch - trying to check current header anyway")

    time.sleep(1.5)

    # Check header
    try:
        win = app.AXMainWindow
        statics = win.findAll(AXRole="AXStaticText")
        for st in statics:
            val = str(getattr(st, "AXValue", "") or "")
            pos = getattr(st, "AXPosition", None)
            if pos and pos[0] < 800 and pos[1] < 100:
                print(f"Header candidate: '{val}' at {pos}")
    except Exception as e:
        print(f"Error reading header: {e}")

    # Step 2: Find 线索详情 links
    print("\n=== 查找线索详情链接 ===")
    links = find_linxiangqing_links(app)
    print(f"Found {len(links)} 线索详情 links")

    if not links:
        print("No links found - trying direct coordinate click from known position")
        # From earlier scan: first visible 线索详情 at pos=(337, 255)
        # But scroll might have changed, use y~255 area
        if pid:
            print("Clicking at known coord (337, 417) - estimated mid of first visible link")
            postclick(pid, 337+36, 417)
            time.sleep(2.0)
    else:
        # Click first link
        lk = links[0]
        pos = getattr(lk, "AXPosition", None)
        if pos and pid:
            cx = pos[0] + 36
            cy = pos[1] + 11
            print(f"Clicking 线索详情 at ({cx}, {cy})")
            postclick(pid, cx, cy)
            time.sleep(2.0)
        else:
            try:
                lk.AXPress()
                print("AXPress on 线索详情 link")
                time.sleep(2.0)
            except Exception as e:
                print(f"AXPress failed: {e}")

    # Step 3: Scan for 去联系
    print("\n=== 扫描去联系 ===")
    results = scan_for_qulianxi(app)
    if results:
        print(f"找到 {len(results)} 个去联系元素!")
        # Click first one
        role, elem, pos = results[0]
        if pos and pid:
            cx = pos[0] + 20
            cy = pos[1] + 10
            print(f"Clicking 去联系 at ({cx}, {cy})")
            postclick(pid, cx, cy)
        else:
            try:
                elem.AXPress()
                print("AXPress on 去联系")
            except Exception as e:
                print(f"AXPress failed: {e}")
    else:
        print("没有找到去联系 - 可能需要点击特定消息后才出现")

        # Dump all visible buttons and links for debugging
        print("\n=== 所有 AXButton ===")
        try:
            win = app.AXMainWindow
            buttons = win.findAll(AXRole="AXButton")
            print(f"共 {len(buttons)} 个 AXButton")
            for b in buttons[:20]:
                title = str(getattr(b, "AXTitle", "") or "")
                pos = getattr(b, "AXPosition", None)
                if title and pos:
                    print(f"  '{title}' at {pos}")
        except Exception as e:
            print(f"Error: {e}")

        print("\n=== 所有 AXLink ===")
        try:
            all_links = win.findAll(AXRole="AXLink")
            print(f"共 {len(all_links)} 个 AXLink")
            for lk in all_links[:20]:
                title = str(getattr(lk, "AXTitle", "") or "")
                pos = getattr(lk, "AXPosition", None)
                if pos:
                    print(f"  '{title}' at {pos}")
        except Exception as e:
            print(f"Error: {e}")

    print("\ndone")

if __name__ == "__main__":
    main()
