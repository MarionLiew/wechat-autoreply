"""
尝试在经营线索模块里找到并点击「去联系」按钮。

策略：
1. 先从 AX 树找经营线索面板里的线索条目坐标
2. 用 Quartz 移动鼠标到该条目触发 hover
3. hover 后再扫描 AX 树找「去联系」按钮
4. 用 Quartz 鼠标点击该按钮
"""
import sys, time
sys.path.insert(0, ".")
import atomacos
import Quartz
from config import settings

app = atomacos.getAppRefByBundleId(settings.wecom_bundle_id)
windows = app.AXWindows
main_win = next(
    (w for w in windows if str(getattr(w, "AXTitle", "") or "") == "企业微信"),
    windows[0],
)

pid = None
try:
    from AppKit import NSWorkspace
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        if a.bundleIdentifier() == settings.wecom_bundle_id:
            pid = int(a.processIdentifier())
            break
except Exception as e:
    print(f"获取 pid 失败: {e}", flush=True)

print(f"pid={pid}", flush=True)

def _safe_children(el):
    try: return el.AXChildren or []
    except: return []

def _deep_find_all(root, role, max_depth=8):
    out = []
    if max_depth < 0: return out
    try:
        if str(getattr(root, "AXRole", "") or "") == role:
            out.append(root)
    except: pass
    for c in _safe_children(root):
        out.extend(_deep_find_all(c, role, max_depth - 1))
    return out

def move_mouse(x, y):
    pt = Quartz.CGPointMake(x, y)
    ev = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, pt, Quartz.kCGMouseButtonLeft)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

def click_at(x, y):
    pt = Quartz.CGPointMake(x, y)
    for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        ev = Quartz.CGEventCreateMouseEvent(None, etype, pt, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.05)

# ── 方法1：先直接扫 AXButton/AXStaticText 找「去联系」(浅层) ──
print("\n== 方法1：浅层扫 AXButton ==", flush=True)
btns = _deep_find_all(main_win, "AXButton", max_depth=8)
print(f"AXButton 共 {len(btns)} 个", flush=True)
lianxi_btn = None
for btn in btns:
    try:
        title = str(getattr(btn, "AXTitle", "") or "").strip()
        if "去联系" in title or title == "联系":
            pos = getattr(btn, "AXPosition", None)
            sz  = getattr(btn, "AXSize", None)
            print(f"  找到: {repr(title)} pos={pos} sz={sz}", flush=True)
            if lianxi_btn is None:
                lianxi_btn = btn
    except: pass

# ── 方法2：找经营线索面板的线索行，hover 触发按钮 ──
if lianxi_btn is None:
    print("\n== 方法2：hover 触发 ==", flush=True)
    # 找所有 AXTable，跳过第一个（会话列表）
    tables = _deep_find_all(main_win, "AXTable", max_depth=6)
    print(f"找到 {len(tables)} 个 AXTable", flush=True)

    # 经营线索面板是宽度最大的 AXTable（会话列表宽~252，线索面板宽~1890）
    lead_table = None
    for tbl in tables:
        pos = getattr(tbl, "AXPosition", None)
        sz  = getattr(tbl, "AXSize", None)
        print(f"  table pos={pos} sz={sz}", flush=True)
    # 取宽度最大的
    if tables:
        lead_table = max(tables, key=lambda t: (getattr(t, "AXSize", None) or [0, 0])[0])
        pos = getattr(lead_table, "AXPosition", None)
        sz  = getattr(lead_table, "AXSize", None)
        print(f"选用最宽 table: pos={pos} sz={sz}", flush=True)

    if lead_table:
        rows = [c for c in _safe_children(lead_table)
                if str(getattr(c, "AXRole", "") or "") == "AXRow"]
        print(f"线索行数: {len(rows)}", flush=True)

        for i, row in enumerate(rows[:5]):  # 试前5行
            pos = getattr(row, "AXPosition", None)
            sz  = getattr(row, "AXSize", None)
            if not pos or not sz:
                continue
            cx = pos[0] + sz[0] / 2
            cy = pos[1] + sz[1] / 2
            print(f"  hover 第{i}行 pos={pos} sz={sz} 中心=({cx:.0f},{cy:.0f})", flush=True)

            # 移动鼠标到该行
            move_mouse(cx, cy)
            time.sleep(0.4)

            # 再扫 AXButton
            btns2 = _deep_find_all(main_win, "AXButton", max_depth=8)
            for btn in btns2:
                try:
                    title = str(getattr(btn, "AXTitle", "") or "").strip()
                    if "去联系" in title or title == "联系":
                        bpos = getattr(btn, "AXPosition", None)
                        print(f"    hover 后找到: {repr(title)} pos={bpos}", flush=True)
                        lianxi_btn = btn
                        break
                except: pass
            if lianxi_btn:
                break
    else:
        print("未找到线索专用 AXTable", flush=True)

# ── 点击 ──
if lianxi_btn:
    pos = getattr(lianxi_btn, "AXPosition", None)
    sz  = getattr(lianxi_btn, "AXSize", None)
    print(f"\n== 点击「去联系」pos={pos} sz={sz} ==", flush=True)
    # 先用 AX Press
    for action in ("Press", "AXPress"):
        fn = getattr(lianxi_btn, action, None)
        if callable(fn):
            try:
                fn()
                print(f"  AX {action} 成功", flush=True)
                break
            except Exception as e:
                print(f"  AX {action} 失败: {e}", flush=True)
    else:
        # 坐标点击
        if pos and sz:
            cx = pos[0] + sz[0] / 2
            cy = pos[1] + sz[1] / 2
            print(f"  坐标点击 ({cx:.0f},{cy:.0f})", flush=True)
            click_at(cx, cy)
    print("完成", flush=True)
else:
    print("\n未找到「去联系」按钮", flush=True)
