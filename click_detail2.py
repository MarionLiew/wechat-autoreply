#!/usr/bin/env python3
"""
Click 线索详情>> link in 经营线索 chat, scan for 去联系
Uses AXWindows instead of AXMainWindow
"""
import time
import subprocess
import atomacos
import Quartz
from config import settings

def get_pid():
    apps = atomacos.NativeUIElement.getRunningApps()
    for a in apps:
        if a.bundleIdentifier() == settings.wecom_bundle_id:
            pid = a.processIdentifier()
            print(f"Found WeCom pid={pid}")
            return pid
    return None

def get_app_windows(pid):
    app = atomacos.getAppRefByPid(pid)
    try:
        wins = app.AXWindows
        print(f"AXWindows count: {len(wins)}")
        return app, wins
    except Exception as e:
        print(f"AXWindows error: {e}")
        return app, []

def postclick(pid, x, y):
    pt = Quartz.CGPointMake(x, y)
    for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        ev = Quartz.CGEventCreateMouseEvent(None, etype, pt, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPostToPid(pid, ev)
        time.sleep(0.05)

def find_window_with_chat(wins):
    """Find the main WeCom window (largest)"""
    best = None
    best_area = 0
    for w in wins:
        try:
            sz = getattr(w, "AXSize", None)
            if sz:
                area = sz[0] * sz[1]
                if area > best_area:
                    best_area = area
                    best = w
        except Exception:
            pass
    return best

def switch_to_jingyingxiansuo(app, win, pid):
    """Try to switch to 经营线索 chat via AXSelected on conversation list row"""
    try:
        lists = win.findAll(AXRole="AXList")
        print(f"Found {len(lists)} AXList")
        for lst in lists:
            try:
                pos = getattr(lst, "AXPosition", None)
                sz = getattr(lst, "AXSize", None)
                print(f"  AXList pos={pos} sz={sz}")
                rows = lst.AXChildren
                for row in rows:
                    try:
                        # Check all descendants for 经营线索 text
                        desc = str(getattr(row, "AXDescription", "") or "")
                        val = str(getattr(row, "AXValue", "") or "")
                        label = str(getattr(row, "AXLabel", "") or "")
                        # Check children too
                        children_text = ""
                        try:
                            for c in row.AXChildren:
                                children_text += str(getattr(c, "AXValue", "") or "")
                                children_text += str(getattr(c, "AXDescription", "") or "")
                        except Exception:
                            pass
                        combined = desc + val + label + children_text
                        if "经营线索" in combined:
                            print(f"Found 经营线索 row! Setting AXSelected=True")
                            row.AXSelected = True
                            return True
                    except Exception:
                        pass
            except Exception as e:
                print(f"  Error on list: {e}")
    except Exception as e:
        print(f"switch error: {e}")
    return False

def find_linxiangqing_links(win):
    """Find 线索详情>> links by traversing chat scroll area"""
    links = []
    try:
        # Find scroll areas
        scroll_areas = win.findAll(AXRole="AXScrollArea")
        print(f"Found {len(scroll_areas)} scroll areas")
        chat_scroll = None
        for sa in scroll_areas:
            sz = getattr(sa, "AXSize", None)
            pos = getattr(sa, "AXPosition", None)
            if sz and sz[0] > 800:
                print(f"  Chat scroll candidate: pos={pos} sz={sz}")
                chat_scroll = sa
                break

        if not chat_scroll:
            return links

        # AXScrollArea → children → find AXTable
        children = chat_scroll.AXChildren
        print(f"Scroll area has {len(children)} direct children")
        for child in children:
            role = getattr(child, "AXRole", "")
            if role == "AXTable":
                rows = child.AXChildren
                print(f"Table has {len(rows)} rows")
                for row in rows:
                    if getattr(row, "AXRole", "") != "AXRow":
                        continue
                    try:
                        cells = row.AXChildren
                        for cell in cells:
                            try:
                                cell_kids = cell.AXChildren
                                for ck in cell_kids:
                                    if getattr(ck, "AXRole", "") == "AXTextArea":
                                        try:
                                            ta_kids = ck.AXChildren
                                            for lk in ta_kids:
                                                if getattr(lk, "AXRole", "") == "AXLink":
                                                    title = str(getattr(lk, "AXTitle", "") or "")
                                                    if "线索详情" in title:
                                                        pos = getattr(lk, "AXPosition", None)
                                                        sz2 = getattr(lk, "AXSize", None)
                                                        print(f"  Found link: '{title}' pos={pos} sz={sz2}")
                                                        links.append(lk)
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                    except Exception:
                        pass
    except Exception as e:
        print(f"find_linxiangqing error: {e}")
    return links

def scan_for_qulianxi(win):
    """Scan the window for 去联系 elements"""
    results = []
    try:
        for role in ("AXButton", "AXLink", "AXStaticText"):
            elems = win.findAll(AXRole=role)
            for e in elems:
                title = str(getattr(e, "AXTitle", "") or "")
                val = str(getattr(e, "AXValue", "") or "")
                desc = str(getattr(e, "AXDescription", "") or "")
                if "去联系" in (title + val + desc):
                    pos = getattr(e, "AXPosition", None)
                    print(f"  {role} 去联系: pos={pos} title='{title}' val='{val}'")
                    results.append((role, e, pos))
    except Exception as e:
        print(f"scan error: {e}")
    return results

def dump_all_buttons(win):
    """Dump buttons for debugging"""
    try:
        buttons = win.findAll(AXRole="AXButton")
        print(f"All AXButtons ({len(buttons)}):")
        for b in buttons[:30]:
            title = str(getattr(b, "AXTitle", "") or "")
            pos = getattr(b, "AXPosition", None)
            print(f"  '{title}' at {pos}")
    except Exception as e:
        print(f"Error: {e}")

    try:
        all_links = win.findAll(AXRole="AXLink")
        print(f"All AXLinks ({len(all_links)}):")
        for lk in all_links[:30]:
            title = str(getattr(lk, "AXTitle", "") or "")
            pos = getattr(lk, "AXPosition", None)
            print(f"  '{title}' at {pos}")
    except Exception as e:
        print(f"Error: {e}")

def main():
    pid = get_pid()
    if not pid:
        print("WeCom not found")
        return

    app, wins = get_app_windows(pid)
    if not wins:
        print("No windows found")
        return

    win = find_window_with_chat(wins)
    if not win:
        print("No main window found")
        return

    sz = getattr(win, "AXSize", None)
    pos = getattr(win, "AXPosition", None)
    print(f"Main window: pos={pos} sz={sz}")

    # Step 1: Switch to 经营线索
    print("\n=== 切换到经营线索 ===")
    switched = switch_to_jingyingxiansuo(app, win, pid)
    print(f"Switched: {switched}")
    time.sleep(1.5)

    # Check header
    try:
        statics = win.findAll(AXRole="AXStaticText")
        for st in statics[:5]:
            val = str(getattr(st, "AXValue", "") or "")
            pos2 = getattr(st, "AXPosition", None)
            if pos2 and pos2[1] < 100:
                print(f"Header: '{val}' at {pos2}")
    except Exception as e:
        print(f"header check error: {e}")

    # Step 2: Find and click 线索详情
    print("\n=== 查找线索详情链接 ===")
    links = find_linxiangqing_links(win)
    print(f"Found {len(links)} 线索详情 links")

    clicked = False
    if links:
        lk = links[0]
        lk_pos = getattr(lk, "AXPosition", None)
        if lk_pos:
            cx = lk_pos[0] + 36
            cy = lk_pos[1] + 11
            print(f"Clicking 线索详情 at ({cx}, {cy})")
            postclick(pid, cx, cy)
            clicked = True
            time.sleep(2.0)

    if not clicked:
        # Use known coordinates from earlier scan
        print("Using hardcoded coords: (373, 266)")
        postclick(pid, 373, 266)
        time.sleep(2.0)

    # Step 3: Scan for 去联系
    print("\n=== 扫描去联系 ===")
    results = scan_for_qulianxi(win)
    if results:
        print(f"找到 {len(results)} 个去联系元素!")
        role, elem, epos = results[0]
        if epos and pid:
            cx = epos[0] + 20
            cy = epos[1] + 10
            print(f"Clicking 去联系 at ({cx}, {cy})")
            postclick(pid, cx, cy)
        else:
            try:
                elem.AXPress()
                print("AXPress 去联系")
            except Exception as e:
                print(f"AXPress failed: {e}")
    else:
        print("没有找到去联系")
        dump_all_buttons(win)

    print("\ndone")

if __name__ == "__main__":
    main()
