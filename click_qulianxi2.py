"""
坐标方式点击经营线索里的「去联系」按钮。
行内容是 Chromium WebView，AX 读不到，只能按位置推算。
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
except Exception: pass

def _safe_children(el):
    try: return el.AXChildren or []
    except: return []

def _deep_find_all(root, role, max_depth=6):
    out = []
    if max_depth < 0: return out
    try:
        if str(getattr(root, "AXRole", "") or "") == role: out.append(root)
    except: pass
    for c in _safe_children(root):
        out.extend(_deep_find_all(c, role, max_depth-1))
    return out

def mouse_move(x, y):
    pt = Quartz.CGPointMake(x, y)
    ev = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, pt, Quartz.kCGMouseButtonLeft)
    Quartz.CGEventPostToPid(pid, ev)

def mouse_click(x, y):
    pt = Quartz.CGPointMake(x, y)
    for etype in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        ev = Quartz.CGEventCreateMouseEvent(None, etype, pt, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPostToPid(pid, ev)
        time.sleep(0.05)

# 找经营线索面板（最宽的 AXTable）
tables = _deep_find_all(main_win, "AXTable", max_depth=6)
lead_table = max(tables, key=lambda t: (getattr(t, "AXSize", None) or [0,0])[0])
tpos = getattr(lead_table, "AXPosition", None)
tsz  = getattr(lead_table, "AXSize", None)
print(f"线索面板: pos={tpos} sz={tsz}", flush=True)

rows = [c for c in _safe_children(lead_table)
        if str(getattr(c, "AXRole","") or "") == "AXRow"]
print(f"行数: {len(rows)}", flush=True)

# 取第一个真实线索行（高度>30，跳过头部行）
lead_rows = [r for r in rows
             if (getattr(r, "AXSize", None) or [0,0])[1] > 30]
print(f"线索行（h>30）: {len(lead_rows)} 个", flush=True)

if not lead_rows:
    print("没找到线索行", flush=True)
    sys.exit(1)

# 取第一行
row = lead_rows[0]
rpos = getattr(row, "AXPosition", None)
rsz  = getattr(row, "AXSize", None)
print(f"第一线索行: pos={rpos} sz={rsz}", flush=True)

# 「去联系」按钮通常在行右侧 1/5 区域（x 约 row_right - 100~200px）
# 先 hover 到行中央，让按钮出现
row_cx = rpos[0] + rsz[0] / 2
row_cy = rpos[1] + rsz[1] / 2
print(f"hover 到行中央 ({row_cx:.0f}, {row_cy:.0f})", flush=True)
mouse_move(row_cx, row_cy)
time.sleep(0.6)

# 尝试在右侧几个 x 位置点击（覆盖可能出现的按钮区域）
row_right = rpos[0] + rsz[0]
test_xs = [
    row_right - 80,   # 最右边
    row_right - 160,  # 次右
    row_right - 240,  # 再左一点
]

for tx in test_xs:
    ty = row_cy
    print(f"  → 点击 ({tx:.0f}, {ty:.0f})", flush=True)
    mouse_move(tx, ty)
    time.sleep(0.3)
    # 扫 AXButton 看有没有「去联系」出现
    btns = _deep_find_all(main_win, "AXButton", max_depth=8)
    for btn in btns:
        title = str(getattr(btn, "AXTitle","") or "").strip()
        if "去联系" in title:
            bpos = getattr(btn,"AXPosition",None)
            print(f"     ★ hover 后出现了「去联系」! pos={bpos}", flush=True)
            # 点击
            fn = getattr(btn, "Press", None)
            if callable(fn):
                try: fn(); print("     AX Press 成功", flush=True); sys.exit(0)
                except Exception as e: print(f"     AX Press 失败: {e}", flush=True)
            # 坐标点击
            if bpos:
                bsz = getattr(btn,"AXSize",None)
                bcx = bpos[0] + (bsz[0]/2 if bsz else 0)
                bcy = bpos[1] + (bsz[1]/2 if bsz else 0)
                mouse_click(bcx, bcy)
                print(f"     坐标点击 ({bcx:.0f},{bcy:.0f}) 完成", flush=True)
                sys.exit(0)

# 没通过 AX 找到，直接坐标点击
print(f"\n直接坐标点击 ({test_xs[0]:.0f}, {row_cy:.0f})", flush=True)
mouse_click(test_xs[0], row_cy)
print("坐标点击完成，请观察企微窗口有没有反应", flush=True)
