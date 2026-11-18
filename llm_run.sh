#!/bin/bash
# llm_run.sh — headless-вызов модели с выбором движка (shared-utils).
#
# Контракт claude_run.sh плюс движок codex. С 02.10.2026 фоновые задания малины
# ходят в Codex через авторизованный ChatGPT-аккаунт (Claude на Пи ушёл в hold 01.10).
#
#   echo "$PROMPT" | llm_run.sh --model MODEL [--engine codex|claude] \
#       [--log FILE] [--out FILE] [--cwd DIR] [--timeout SEC] \
#       [--permission-mode MODE] [--effort LEVEL] [--add-dir DIR] [--tools "Bash(git:*)" ...]
#
# Движок: --engine, иначе $LLM_ENGINE, иначе claude. Промпт идёт в codex через
# stdin: срез планёрки до 300 тыс. символов в argv не влезает.
# --tools действуют только для claude. У codex --permission-mode выбирает
# песочницу: acceptEdits (дефолт) — workspace-write в --cwd, любой другой
# режим — read-only.
# --access none|read|write (только Codex) изолирует от user config/MCP/plugins/AGENTS,
# выбирает sandbox; none также отключает инструменты. Без флага прежнее поведение.
# Ответ — в --out (иначе в stdout). Код возврата — от движка (124 — таймаут,
# 127 — нет бинаря). --effort у codex — model_reasoning_effort, у claude — --effort. Пустой ответ при rc=0 даёт rc=99.
set -u
set -m

ENGINE="${LLM_ENGINE:-claude}" MODEL="" LOGF="" OUTF="" CWD="" TIMEOUT="" PERM="acceptEdits" EFFORT="" ACCESS=""
TOOLS=() ADD_DIRS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --access)
      [ $# -ge 2 ] || { echo "llm_run.sh: --access требует значение" >&2; exit 2; }
      ACCESS="$2"; shift 2;;
    --engine) ENGINE="$2"; shift 2;;
    --model) MODEL="$2"; shift 2;;
    --log) LOGF="$2"; shift 2;;
    --out) OUTF="$2"; shift 2;;
    --cwd) CWD="$2"; shift 2;;
    --timeout) TIMEOUT="$2"; shift 2;;
    --permission-mode) PERM="$2"; shift 2;;
    --tools) TOOLS+=("$2"); shift 2;;
    --effort)
      [ $# -ge 2 ] || { echo "llm_run.sh: --effort требует значение" >&2; exit 2; }
      EFFORT="$2"; shift 2;;
    --add-dir)
      [ $# -ge 2 ] || { echo "llm_run.sh: --add-dir требует значение" >&2; exit 2; }
      ADD_DIRS+=("$2"); shift 2;;
    *) echo "llm_run.sh: неизвестный аргумент $1" >&2; exit 2;;
  esac
done
[ -z "$MODEL" ] && { echo "llm_run.sh: --model обязателен" >&2; exit 2; }
case "$ENGINE" in codex|claude) ;; *) echo "llm_run.sh: неизвестный движок $ENGINE" >&2; exit 2;; esac

case "$ACCESS" in ""|none|read|write) ;; *) echo "llm_run.sh: неизвестный --access" >&2; exit 2;; esac
[ -n "$ACCESS" ] && [ "$ENGINE" != codex ] && { echo "llm_run.sh: --access только для codex" >&2; exit 2; }

log() { [ -n "$LOGF" ] && echo "[$(date '+%Y-%m-%d %H:%M:%S')] llm_run: $*" >> "$LOGF"; }

if [ "$ENGINE" = codex ]; then
  BIN="${CODEX_BIN:-$(command -v codex || true)}"
  [ -z "$BIN" ] && [ -x /opt/homebrew/bin/codex ] && BIN=/opt/homebrew/bin/codex
else
  BIN="${CLAUDE_BIN:-$(command -v claude || true)}"
  [ -z "$BIN" ] && [ -x /opt/homebrew/bin/claude ] && BIN=/opt/homebrew/bin/claude
fi
if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then
  log "ERROR: бинарь $ENGINE не найден"; echo "llm_run.sh: бинарь $ENGINE не найден" >&2; exit 127
fi

if [ -n "$CWD" ]; then
  cd -- "$CWD" || { echo "llm_run.sh: не удалось перейти в каталог: $CWD" >&2; exit 2; }
fi
TMP=$(mktemp) PROMPTF=$(mktemp) ERRF=$(mktemp)
trap 'rm -f "$TMP" "$PROMPTF" "$ERRF"' EXIT
cat > "$PROMPTF"

if [ "$PERM" = acceptEdits ]; then SANDBOX=workspace-write; else SANDBOX=read-only; fi
case "$ACCESS" in none|read) SANDBOX=read-only;; write) SANDBOX=workspace-write;; esac
if [ "$ENGINE" = codex ]; then
  ARGS=(exec --skip-git-repo-check --ephemeral --color never -m "$MODEL"
        -s "$SANDBOX" -C "$(pwd)" -o "$TMP")
  [ -n "$EFFORT" ] && ARGS+=(-c "model_reasoning_effort=$EFFORT")
  if [ -n "$ACCESS" ]; then
    ARGS+=(--ignore-user-config --ignore-rules -c project_doc_max_bytes=0 -c 'web_search="disabled"'
           --disable apps --disable plugins --disable computer_use --disable browser_use
           --disable browser_use_external --disable in_app_browser --disable image_generation
           --disable view_image --disable multi_agent)
    [ "$ACCESS" = none ] && ARGS+=(--disable code_mode_host --disable shell_tool --disable unified_exec)
  fi
  for d in ${ADD_DIRS[@]+"${ADD_DIRS[@]}"}; do ARGS+=(--add-dir "$d"); done
  ARGS+=(-)
else
  ARGS=(-p "$(cat "$PROMPTF")" --model "$MODEL" --permission-mode "$PERM" --output-format text)
  [ -n "$EFFORT" ] && ARGS+=(--effort "$EFFORT")
  for d in ${ADD_DIRS[@]+"${ADD_DIRS[@]}"}; do ARGS+=(--add-dir "$d"); done
  [ ${#TOOLS[@]} -gt 0 ] && ARGS+=(--allowedTools "${TOOLS[@]}")
fi

# codex пишет в stdout ход работы, ответ — только в -o; claude отвечает в stdout
if [ "$ENGINE" = codex ]; then STDOUT_TO=/dev/null; else STDOUT_TO="$TMP"; fi

START=$SECONDS
"$BIN" "${ARGS[@]}" < "$PROMPTF" > "$STDOUT_TO" 2> "$ERRF" &
PID=$!
RC=""
if [ -n "$TIMEOUT" ]; then
  # свой сторож: на Маке нет coreutils timeout
  while kill -0 "$PID" 2>/dev/null; do
    if [ $((SECONDS - START)) -ge "$TIMEOUT" ]; then
      kill -TERM -- "-$PID" 2>/dev/null; sleep 1; kill -KILL -- "-$PID" 2>/dev/null
      wait "$PID" 2>/dev/null; RC=124; break
    fi
    sleep 0.2
  done
fi
[ -z "$RC" ] && { wait "$PID"; RC=$?; }
SEC=$((SECONDS - START))
LEN=$(wc -c < "$TMP" | tr -d ' ')

[ -n "$LOGF" ] && cat "$ERRF" >> "$LOGF"
if [ $RC -ne 0 ] || [ "$LEN" = "0" ]; then
  [ $RC -eq 0 ] && RC=99
  log "ERROR: $ENGINE rc=$RC за ${SEC}с, ответ $LEN байт; stdout: $(head -c 2000 "$TMP"); stderr: $(tail -c 2000 "$ERRF")"
else
  log "ok: $ENGINE rc=0 за ${SEC}с, ответ $LEN байт (модель $MODEL)"
fi

if [ -n "$OUTF" ]; then
  if [ "$RC" -eq 0 ]; then mv "$TMP" "$OUTF" || RC=$?; else rm -f "$TMP"; fi
else
  cat "$TMP"; rm -f "$TMP"
fi
exit $RC
