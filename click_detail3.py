#!/usr/bin/env python3
"""
Use AXSplitGroup as root for traversal — findAll from window was missing content.
Click 线索详情 in 经营线索 chat, then scan for 去联系.
"""
import time
import atomacos
import Quartz
from config import settings

def get_app():
    return atomacos.getAppRefByBundleId(settings.wecom_bundle_id)

def get_pid():
    apps = atomacos.NativeUIElement.getRunningApps()
    for a in apps:
        if a.bundleIdentifier() == settings.wecom_bundle_id:
            return a.processIdentifier()
    return None

def postclick(pid, x, y):
    pt = Quartz.CGPointMake(x, y)
    for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        ev = Quartz.CGEventCreateMouseEvent(None, etype, pt, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPostToPid(pid, ev)
        time.sleep(0.05)

def get_split_group(app):
    try:
        win = app.AXWindows[0]
        for child in win.AXChildren:
            if getattr(child, "AXRole", "") == "AXSplitGroup":
                return child
    except Exception as e:
        print(f"get_split_group error: {e}")
    return None

def find_in_subtree(root, role, max_depth=8, _depth=0):
    """Recursively find all elements with given AXRole"""
    results = []
    if _depth > max_depth:
        return results
    try:
        r = getattr(root, "AXRole", "")
        if r == role:
            results.append(root)
        children = root.AXChildren
        for c in children:
            results.extend(find_in_subtree(c, role, max_depth, _depth+1))
    except Exception:
        pass
    return results

def switch_to_jingyingxiansuo(sg, pid):
    """Find 经营线索 row in conversation list and set AXSelected=True"""
    # Find AXList elements in split group
    lists = find_in_subtree(sg, "AXList", max_depth=5)
    print(f"Found {len(lists)} AXList elements")
    for lst in lists:
        pos = getattr(lst, "AXPosition", None)
        sz = getattr(lst, "AXSize", None)
        print(f"  AXList: pos={pos} sz={sz}")
        try:
            rows = lst.AXChildren
            print(f"  Rows: {len(rows)}")
            for row in rows:
                try:
                    children = row.AXChildren
                    all_text = ""
                    for c in children:
                        all_text += str(getattr(c, "AXValue", "") or "")
                        all_text += str(getattr(c, "AXDescription", "") or "")
                    # Also check AXStaticText grandchildren
                    for c in children:
                        try:
                            for gc in c.AXChildren:
                                all_text += str(getattr(gc, "AXValue", "") or "")
                        except Exception:
                            pass
                    if "经营线索" in all_text:
                        print(f"Found 经营线索 row! Text: '{all_text[:60]}'")
                        row.AXSelected = True
                        return True
                except Exception:
                    pass
        except Exception as e:
            print(f"  Error: {e}")
    return False

def find_linxiangqing_links(sg):
    """Find 线索详情>> links in the chat scroll area"""
    links = []
    # Find scroll areas
    scroll_areas = find_in_subtree(sg, "AXScrollArea", max_depth=4)
    print(f"Found {len(scroll_areas)} scroll areas")

    chat_scroll = None
    for sa in scroll_areas:
        sz = getattr(sa, "AXSize", None)
        pos = getattr(sa, "AXPosition", None)
        print(f"  ScrollArea: pos={pos} sz={sz}")
        if sz and sz[0] > 800:
            chat_scroll = sa
            break

    if not chat_scroll:
        print("No chat scroll area found")
        return links

    # Traverse: scroll → table → rows → cells → textareas → links
    try:
        scroll_kids = chat_scroll.AXChildren
        print(f"Chat scroll children: {len(scroll_kids)}")
        for child in scroll_kids:
            role = getattr(child, "AXRole", "")
            print(f"  child role={role}")
            if role == "AXTable":
                rows = child.AXChildren
                print(f"  Table rows: {len(rows)}")
                for row in rows:
                    if getattr(row, "AXRole", "") != "AXRow":
                        continue
                    try:
                        cells = row.AXChildren
                        for cell in cells:
                            try:
                                for ck in cell.AXChildren:
                                    if getattr(ck, "AXRole", "") == "AXTextArea":
                                        try:
                                            for lk in ck.AXChildren:
                                                if getattr(lk, "AXRole", "") == "AXLink":
                                                    title = str(getattr(lk, "AXTitle", "") or "")
                                                    if "线索详情" in title:
                                                        pos2 = getattr(lk, "AXPosition", None)
                                                        print(f"Found link '{title}' at {pos2}")
                                                        links.append(lk)
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                    except Exception:
                        pass
    except Exception as e:
        print(f"Traversal error: {e}")
    return links

def scan_for_qulianxi(sg):
    """Look for 去联系 in the full split group tree"""
    print("Scanning for 去联系...")
    results = []
    for role in ("AXButton", "AXLink", "AXStaticText"):
        elems = find_in_subtree(sg, role, max_depth=10)
        for e in elems:
            title = str(getattr(e, "AXTitle", "") or "")
            val = str(getattr(e, "AXValue", "") or "")
            desc = str(getattr(e, "AXDescription", "") or "")
            combined = title + val + desc
            if "去联系" in combined:
                pos = getattr(e, "AXPosition", None)
                print(f"  {role} 去联系: pos={pos}")
                results.append((role, e, pos))
    return results

def dump_visible_buttons(sg):
    elems = find_in_subtree(sg, "AXButton", max_depth=6)
    print(f"All buttons ({len(elems)}):")
    for e in elems[:30]:
        title = str(getattr(e, "AXTitle", "") or "")
        pos = getattr(e, "AXPosition", None)
        if title:
            print(f"  '{title}' at {pos}")

    links = find_in_subtree(sg, "AXLink", max_depth=6)
    print(f"\nAll links ({len(links)}):")
    for lk in links[:30]:
        title = str(getattr(lk, "AXTitle", "") or "")
        pos = getattr(lk, "AXPosition", None)
        print(f"  '{title}' at {pos}")

def main():
    app = get_app()
    pid = get_pid()
    print(f"PID: {pid}")

    sg = get_split_group(app)
    if sg is None:
        print("No AXSplitGroup found")
        return
    print(f"SplitGroup: pos={getattr(sg,'AXPosition',None)} sz={getattr(sg,'AXSize',None)}")

    # Step 1: Switch to 经营线索
    print("\n=== 切换到经营线索 ===")
    switched = switch_to_jingyingxiansuo(sg, pid)
    print(f"Switched: {switched}")
    time.sleep(1.5)

    # Step 2: Find 线索详情 links
    print("\n=== 查找线索详情链接 ===")
    links = find_linxiangqing_links(sg)
    print(f"Found {len(links)} links")

    clicked = False
    if links:
        lk = links[0]
        lk_pos = getattr(lk, "AXPosition", None)
        if lk_pos and pid:
            cx = lk_pos[0] + 36
            cy = lk_pos[1] + 11
            print(f"Clicking 线索详情 at ({cx}, {cy})")
            postclick(pid, cx, cy)
            clicked = True
            time.sleep(2.5)

    if not clicked and pid:
        # Try AXPress on first link
        if links:
            try:
                links[0].AXPress()
                print("AXPress on first link")
                clicked = True
                time.sleep(2.5)
            except Exception as e:
                print(f"AXPress failed: {e}")

    # Step 3: Scan for 去联系
    print("\n=== 扫描去联系 ===")
    results = scan_for_qulianxi(sg)
    if results:
        print(f"找到 {len(results)} 个去联系!")
        role, elem, epos = results[0]
        if epos and pid:
            cx = epos[0] + 20
            cy = epos[1] + 10
            print(f"Clicking at ({cx}, {cy})")
            postclick(pid, cx, cy)
        else:
            try:
                elem.AXPress()
                print("AXPress 去联系")
            except Exception as e:
                print(f"AXPress failed: {e}")
    else:
        print("没找到去联系 - dumping buttons/links for debug")
        dump_visible_buttons(sg)

    print("\ndone")

if __name__ == "__main__":
    main()
