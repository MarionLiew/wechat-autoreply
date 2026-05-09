import sys
sys.path.insert(0, ".")
import atomacos
from config import settings

app = atomacos.getAppRefByBundleId(settings.wecom_bundle_id)
windows = app.AXWindows
main_win = next(
    (w for w in windows if str(getattr(w,"AXTitle","") or "")=="企业微信"),
    windows[0],
)

def _safe_children(el):
    try: return el.AXChildren or []
    except: return []

def _deep_find_all(root, role, max_depth=10):
    out = []
    if max_depth < 0: return out
    try:
        if str(getattr(root,"AXRole","") or "") == role: out.append(root)
    except: pass
    for c in _safe_children(root):
        out.extend(_deep_find_all(c, role, max_depth-1))
    return out

print("搜索「去联系」...", flush=True)
found = 0
for role in ("AXButton", "AXStaticText", "AXLink", "AXCell", "AXTextField"):
    for el in _deep_find_all(main_win, role, max_depth=15):
        try:
            title = str(getattr(el,"AXTitle","") or "").strip()
            val   = str(getattr(el,"AXValue","") or "").strip()
            text  = title or val
            if "去联系" in text or ("联系" in text and len(text) < 10):
                pos = getattr(el,"AXPosition",None)
                sz  = getattr(el,"AXSize",None)
                print(f"  [{role}] {repr(text)} pos={pos} sz={sz}", flush=True)
                found += 1
        except: pass
print(f"共找到 {found} 个「联系」相关元素", flush=True)

print("\n所有 AXButton（取前40）:", flush=True)
btns = _deep_find_all(main_win, "AXButton", max_depth=12)
print(f"总计 {len(btns)} 个", flush=True)
seen = set()
for btn in btns:
    try:
        title = str(getattr(btn,"AXTitle","") or "").strip()
        if title and title not in seen:
            seen.add(title)
            pos = getattr(btn,"AXPosition",None)
            print(f"  {repr(title)} pos={pos}", flush=True)
    except: pass
print("done", flush=True)
