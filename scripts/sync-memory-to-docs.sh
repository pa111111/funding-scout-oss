#!/usr/bin/env bash
# PostToolUse hook (Write|Edit): когда правится файл в Claude memory этого проекта —
# зеркалирует всю memory/*.md в D:/Projects/funding-scout/docs/.
# Master-копия в C:\Users\user\.claude\projects\D--Projects-funding-scout\memory\
# Стратегия "sync all on any change": проще и надёжнее, чем нормализовать одиночный путь
# (8 мелких файлов копируются мгновенно, а path-нормализация на смешанных слешах хрупкая).
# Stdin = hook JSON. Парсим через node (jq/python в этой среде недоступны).

fp=$(node -e "let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{try{const o=JSON.parse(d);process.stdout.write(o.tool_input?.file_path||'')}catch(e){}})" 2>/dev/null)

case "$fp" in
  *D--Projects-funding-scout*memory*.md)
    mkdir -p "D:/Projects/funding-scout/docs"
    cp "C:/Users/user/.claude/projects/D--Projects-funding-scout/memory/"*.md "D:/Projects/funding-scout/docs/" 2>/dev/null
    ;;
esac

exit 0
