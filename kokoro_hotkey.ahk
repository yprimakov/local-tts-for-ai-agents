#Requires AutoHotkey v2.0
#SingleInstance Force

; ============================================================
;  Local TTS for AI Agents — Global Hotkeys
;  Paths are resolved relative to this script's location,
;  so the repo can be installed anywhere.
; ============================================================

PYTHON  := A_ScriptDir . "\venv\Scripts\pythonw.exe"
SCRIPT  := A_ScriptDir . "\tts.py"
VOICE   := "af_heart"   ; change voice here (see --list-voices)
SPEED   := "1.0"        ; change speed here (0.5 – 2.0)

TMPFILE := A_Temp . "\kokoro_input.txt"

global activePID := 0


; ── Ctrl+Alt+R — read selected text ─────────────────────────
^!r:: {
    global activePID

    ; If a player is already open, close it first
    if activePID {
        ProcessClose(activePID)
        activePID := 0
        Sleep(80)
    }

    ; Grab selected text via clipboard
    savedClip := ClipboardAll()
    A_Clipboard := ""
    Send "^c"
    if !ClipWait(1) {
        A_Clipboard := savedClip
        return
    }
    text := A_Clipboard
    A_Clipboard := savedClip

    if (Trim(text) = "")
        return

    ; Write to temp file — avoids all shell quoting/escaping issues
    try FileDelete(TMPFILE)
    FileAppend(text, TMPFILE, "UTF-8")

    ; Launch player (pythonw = no console window)
    cmd := '"' . PYTHON . '" "' . SCRIPT . '"'
        . ' --file "' . TMPFILE . '"'
        . ' --voice ' . VOICE
        . ' --speed ' . SPEED
        . ' --play'

    Run(cmd, , "Hide", &activePID)
}


; ── Ctrl+Alt+V — toggle Claude Code voice responses ─────────
^!v:: {
    toggleFile := EnvGet("USERPROFILE") . "\.claude\voice_enabled"
    if FileExist(toggleFile) {
        FileDelete(toggleFile)
        TrayTip("Kokoro TTS", "Voice responses: OFF", 2)
    } else {
        FileAppend("", toggleFile)
        TrayTip("Kokoro TTS", "Voice responses: ON", 2)
    }
}


; ── Ctrl+Alt+S — stop / close player ────────────────────────
^!s:: {
    global activePID
    if activePID {
        ProcessClose(activePID)
        activePID := 0
    }
}
