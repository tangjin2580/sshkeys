import re

js_path = 'G:/code/sshkeys/static/script.js'
js = open(js_path, 'r', encoding='utf-8').read()

# 找到 async function loadConnections() 的起止位置
idx = js.find('async function loadConnections()')
print(f"loadConnections starts at: {idx}")
print(f"Context: {repr(js[idx:idx+100])}")

# 手动找对应的结束大括号
if idx >= 0:
    brace = 0
    pos = idx
    while pos < len(js):
        if js[pos] == '{':
            brace += 1
        elif js[pos] == '}':
            brace -= 1
            if brace == 0:
                end = pos + 1
                break
        pos += 1
    fn_text = js[idx:end]
    print(f"Function length: {len(fn_text)}")
    print(f"First 500 chars: {repr(fn_text[:500])}")
    print(f"Last 200 chars: {repr(fn_text[-200:])}")
    
    # 写入临时文件供检查
    open('G:/code/sshkeys/debug_old_load.txt', 'w', encoding='utf-8').write(fn_text)
    print("Written to debug_old_load.txt")
