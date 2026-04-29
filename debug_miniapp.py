import sys, re

with open('app/static/js/miniapp.js') as f:
    content = f.read()

with open('miniapp_analysis.txt', 'w') as out:
    out.write(f"=== File size: {len(content)} chars ===\n\n")

    # 1. Find GLOBAL_INIT_DATA assignments
    out.write("=== GLOBAL_INIT_DATA assignments ===\n")
    for m in re.finditer(r'(?:let|const|var)?\s*GLOBAL_INIT_DATA\s*=[^;,}]*', content):
        start = max(0, m.start() - 80)
        end = min(len(content), m.end() + 120)
        out.write(content[start:end] + "\n---\n")
    out.write("\n")

    # 2. Find escapeHtml definition
    out.write("=== escapeHtml ===\n")
    for m in re.finditer(r'function\s+escapeHtml|escapeHtml\s*=', content):
        start = max(0, m.start() - 40)
        end = min(len(content), m.end() + 200)
        out.write(content[start:end] + "\n---\n")
    out.write("\n")

    # 3. Find init/start entry points
    out.write("=== Entry points (init/start/DOMContentLoaded/ready) ===\n")
    for m in re.finditer(r'(?:function\s+init|function\s+start|DOMContentLoaded|\.ready\s*\()|document\.addEventListener', content):
        start = max(0, m.start() - 80)
        end = min(len(content), m.end() + 300)
        out.write(content[start:end] + "\n---\n")
    out.write("\n")

    # 4. Find tg.initData usage
    out.write("=== tg.initData / initDataUnsafe ===\n")
    for m in re.finditer(r'tg\.initData|initDataUnsafe|window\.Telegram|WebApp\.initData', content):
        start = max(0, m.start() - 60)
        end = min(len(content), m.end() + 100)
        out.write(content[start:end] + "\n---\n")
    out.write("\n")

    # 5. Find the first call to loadHome / loadSettings
    out.write("=== loadHome / loadSettings calls ===\n")
    for m in re.finditer(r'loadHome\s*\(\)|loadSettings\s*\(\)', content):
        start = max(0, m.start() - 100)
        end = min(len(content), m.end() + 100)
        out.write(content[start:end] + "\n---\n")
    out.write("\n")

    # 6. Find if there's a newline or macro-line-count
    out.write(f"=== Newline count: {content.count(chr(10))} ===\n")
    lines = content.splitlines()
    out.write(f"=== Total logical lines: {len(lines)} ===\n")
    out.write(f"=== Max line length: {max(len(l) for l in lines)} ===\n")
