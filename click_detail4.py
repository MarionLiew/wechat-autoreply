#!/usr/bin/env python3
"""
1. Switch to 经营线索 chat (AXSelected=True on conv row)
2. Click 线索详情>> link
3. Wait for webview to load
4. Find & click 去联系 in the new webview panel
"""
import time
import atomacos
import Quartz
from config import settings

def get_app_pid():
    apps = atomacos.NativeUIElement.getRunningApps()
    for a in apps:
        if a.bundleIdentifier() == settings.wecom_bundle_id:
            return atomacos.getAppRefByBundleId(settings.wecom_bundle_id), a.processIdentifier()
    return None, None

def postclick(pid, x, y):
    pt = Quartz.CGPointMake(x, y)
    for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        ev = Quartz.CGEventCreateMouseEvent(None, etype, pt, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPostToPid(pid, ev)
        time.sleep(0.05)

def get_split_group(app):
    try:
        win = app.AXWindows[0]
        for c in win.AXChildren:
            if getattr(c, "AXRole", "") == "AXSplitGroup":
                return c
    except Exception as e:
        print(f"get_split_group: {e}")
    return None

def switch_to_jingyingxiansuo(sg):
    """Traverse conv list (not deeply) to find 经营线索 row"""
    try:
        # SplitGroup → children → find the narrow left panel (conv list column)
        sg_kids = sg.AXChildren
        print(f"SplitGroup has {len(sg_kids)} children")
        for kid in sg_kids:
            role = getattr(kid, "AXRole", "")
            sz = getattr(kid, "AXSize", None)
            pos = getattr(kid, "AXPosition", None)
            print(f"  {role} pos={pos} sz={sz}")
    except Exception as e:
        print(f"sg_kids error: {e}")
        return False

    # Find AXGroup or AXScrollArea in the left panel for conv list
    # Approach: find AXRow elements at depth 4-5 that contain 经营线索 text
    try:
        sg_kids = sg.AXChildren
        for kid in sg_kids:
            sz = getattr(kid, "AXSize", None)
            if not sz:
                continue
            # Conv list column is usually narrow (< 400px wide)
            if sz[0] > 400:
                continue
            print(f"Checking narrow child for conv list: sz={sz}")
            # Go 2-3 levels deeper to find rows
            try:
                for l1 in kid.AXChildren:
                    role1 = getattr(l1, "AXRole", "")
                    if role1 in ("AXList", "AXOutline", "AXScrollArea"):
                        # Found a list - check its rows
                        try:
                            items = l1.AXChildren
                            print(f"  {role1} has {len(items)} items")
                            for item in items:
                                try:
                                    # Check 2 levels of children for text
                                    kids = item.AXChildren
                                    all_text = ""
                                    for c in kids:
                                        all_text += str(getattr(c, "AXValue", "") or "")
                                        all_text += str(getattr(c, "AXDescription", "") or "")
                                        try:
                                            for gc in c.AXChildren:
                                                all_text += str(getattr(gc, "AXValue", "") or "")
                                        except Exception:
                                            pass
                                    if "经营线索" in all_text:
                                        print(f"Found 经营线索! Setting AXSelected")
                                        item.AXSelected = True
                                        return True
                                except Exception:
                                    pass
                        except Exception as e:
                            print(f"  list error: {e}")
            except Exception as e:
                print(f"  narrow child error: {e}")
    except Exception as e:
        print(f"switch error: {e}")
    return False

def find_chat_scroll_area(sg):
    """Find the wide chat scroll area (> 800px wide)"""
    try:
        sg_kids = sg.AXChildren
        for kid in sg_kids:
            sz = getattr(kid, "AXSize", None)
            pos = getattr(kid, "AXPosition", None)
            if not sz:
                continue
            # The chat+right panel area
            if sz[0] > 800:
                # Look for AXScrollArea within this kid
                try:
                    for l1 in kid.AXChildren:
                        role1 = getattr(l1, "AXRole", "")
                        sz1 = getattr(l1, "AXSize", None)
                        if role1 == "AXScrollArea" and sz1 and sz1[0] > 800:
                            print(f"Found chat scroll: pos={getattr(l1,'AXPosition',None)} sz={sz1}")
                            return l1
                        # Another level
                        try:
                            for l2 in l1.AXChildren:
                                role2 = getattr(l2, "AXRole", "")
                                sz2 = getattr(l2, "AXSize", None)
                                if role2 == "AXScrollArea" and sz2 and sz2[0] > 800:
                                    print(f"Found chat scroll L2: pos={getattr(l2,'AXPosition',None)} sz={sz2}")
                                    return l2
                        except Exception:
                            pass
                except Exception as e:
                    print(f"  scroll search error: {e}")
    except Exception as e:
        print(f"find_chat_scroll: {e}")
    return None

def find_linxiangqing_links_in_scroll(scroll):
    """Find 线索详情>> links in chat scroll area"""
    links = []
    try:
        for child in scroll.AXChildren:
            if getattr(child, "AXRole", "") != "AXTable":
                continue
            rows = child.AXChildren
            print(f"Table: {len(rows)} rows")
            for row in rows:
                if getattr(row, "AXRole", "") != "AXRow":
                    continue
                try:
                    for cell in row.AXChildren:
                        try:
                            for ck in cell.AXChildren:
                                if getattr(ck, "AXRole", "") == "AXTextArea":
                                    try:
                                        for lk in ck.AXChildren:
                                            if getattr(lk, "AXRole", "") == "AXLink":
                                                title = str(getattr(lk, "AXTitle", "") or "")
                                                if "线索详情" in title:
                                                    pos = getattr(lk, "AXPosition", None)
                                                    print(f"  Link '{title}' at {pos}")
                                                    links.append(lk)
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                except Exception:
                    pass
    except Exception as e:
        print(f"find_links error: {e}")
    return links

def scan_for_qulianxi_in_sg(sg, max_depth=8):
    """Find 去联系 button/link in the split group tree"""
    def _search(node, depth):
        results = []
        if depth > max_depth:
            return results
        try:
            role = getattr(node, "AXRole", "")
            title = str(getattr(node, "AXTitle", "") or "")
            val = str(getattr(node, "AXValue", "") or "")
            desc = str(getattr(node, "AXDescription", "") or "")
            if "去联系" in (title + val + desc):
                pos = getattr(node, "AXPosition", None)
                print(f"  Found {role} '去联系' at {pos}")
                results.append((role, node, pos))
            for c in node.AXChildren:
                results.extend(_search(c, depth + 1))
        except Exception:
            pass
        return results
    return _search(sg, 0)

def main():
    app, pid = get_app_pid()
    if not app or not pid:
        print("WeCom not found")
        return
    print(f"PID: {pid}")

    sg = get_split_group(app)
    if not sg:
        print("No SplitGroup")
        return

    # Step 1: Switch to 经营线索
    print("\n=== 切换到经营线索 ===")
    switched = switch_to_jingyingxiansuo(sg)
    print(f"Switched: {switched}")
    if switched:
        time.sleep(1.5)

    # Step 2: Find chat scroll area
    print("\n=== 查找聊天区域 ===")
    scroll = find_chat_scroll_area(sg)
    if not scroll:
        print("Chat scroll not found - trying direct approach with known coords")
        # Use coords from earlier scan - 线索详情>> was at pos=(337, 255) for first visible row
        # Scale to current window: earlier window was ~1888 wide, now 2199 wide
        # Chat area probably starts similarly but let's try same coords
        print("Clicking at (373, 266)")
        postclick(pid, 373, 266)
    else:
        # Step 2b: Find and click 线索详情>> link
        print("\n=== 查找线索详情链接 ===")
        links = find_linxiangqing_links_in_scroll(scroll)
        print(f"Found {len(links)} links")

        if links:
            lk = links[0]
            lk_pos = getattr(lk, "AXPosition", None)
            if lk_pos:
                cx = lk_pos[0] + 36
                cy = lk_pos[1] + 11
                print(f"Clicking 线索详情 at ({cx}, {cy})")
                postclick(pid, cx, cy)
            else:
                try:
                    links[0].AXPress()
                    print("AXPress 线索详情")
                except Exception as e:
                    print(f"AXPress failed: {e}")
        else:
            print("No links found")

    # Step 3: Wait for webview to load
    print("\n=== 等待 webview 加载 (3s) ===")
    time.sleep(3.0)

    # Step 4: Scan for 去联系
    print("\n=== 扫描去联系 ===")
    results = scan_for_qulianxi_in_sg(sg)
    if results:
        print(f"找到 {len(results)} 个去联系元素!")
        role, elem, epos = results[0]
        if epos:
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
        print("没找到去联系")
        # Dump AXWebArea info for debugging
        print("\n=== AXWebArea 信息 ===")
        def find_webareas(node, depth=0):
            if depth > 6:
                return
            try:
                role = getattr(node, "AXRole", "")
                if role == "AXWebArea":
                    pos = getattr(node, "AXPosition", None)
                    sz = getattr(node, "AXSize", None)
                    desc = getattr(node, "AXDescription", None)
                    print(f"  AXWebArea: desc={desc} pos={pos} sz={sz}")
                for c in node.AXChildren:
                    find_webareas(c, depth+1)
            except Exception:
                pass
        find_webareas(sg)

    print("\ndone")

if __name__ == "__main__":
    main()
