# name=Launchpad X - FL Groovebox
# FL Studio MIDI script: FLpad-style groovebox workflow for the Novation Launchpad X.
# Uses the Launchpad X's Programmer mode (pads = notes 11-88, round buttons = CC).
#
# SEQ mode  : 4 channels x 16 steps (2 rows per channel). Tap pads to toggle steps.
# PLAY mode : play the selected channel from the grid (velocity sensitive).
# MIX mode  : bottom row = mute, 2nd row = solo, upper rows = volume (8 tracks/page).
#
# Round buttons (top):   91 = page up     92 = page down  (channels or mixer tracks)
# Right-hand buttons:    89 = SEQ  79 = PLAY  69 = MIX
#                        19 = play/stop  29 = record  39 = prev pattern  49 = next pattern

import device
import channels
import mixer
import transport
import patterns
import general
import midi
import utils

HDR = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0C]
MODE_SEQ, MODE_PLAY, MODE_MIX = 0, 1, 2
OFF = (0, 0, 0)
WHITE = (127, 127, 127)

mode = MODE_SEQ
chan_page = 0      # first visible channel = chan_page * 4
mix_page = 0       # first visible mixer track = 1 + mix_page * 8
last_frame = {}
last_step = -1
held = {}          # pad note -> (channel, note) so we can send note-offs


# ---------------------------------------------------------------- helpers
def sysex(data):
    device.midiOutSysex(bytes(data))


def set_leds(frame):
    """Send only the LEDs that changed, as RGB SysEx (values 0-127)."""
    changed = [(i, c) for i, c in frame.items() if last_frame.get(i) != c]
    for n in range(0, len(changed), 32):
        msg = HDR + [0x03]
        for idx, (r, g, b) in changed[n:n + 32]:
            msg += [3, idx, r, g, b]
        msg.append(0xF7)
        sysex(msg)
    last_frame.update(frame)


def scale(c, k):
    return (int(c[0] * k), int(c[1] * k), int(c[2] * k))


def chan_rgb(ch):
    r, g, b = utils.ColorToRGB(channels.getChannelColor(ch))
    return (max(r >> 1, 8), max(g >> 1, 8), max(b >> 1, 8))


def cur_step():
    if not transport.isPlaying():
        return -1
    ppq = general.getRecPPQ()
    ticks = transport.getSongPos(midi.SONGLENGTH_ABSTICKS)
    return int(ticks // (ppq / 4)) % 16


# ---------------------------------------------------------------- drawing
def draw():
    f = {}
    for r in range(1, 9):
        for c in range(1, 9):
            f[r * 10 + c] = OFF

    step_now = cur_step()
    count = channels.channelCount()

    if mode == MODE_SEQ:
        sel = channels.selectedChannel()
        for slot in range(4):
            ch = chan_page * 4 + slot
            if ch >= count:
                continue
            col = chan_rgb(ch)
            for half in range(2):
                row = 8 - slot * 2 - half
                for k in range(8):
                    step = half * 8 + k
                    if channels.getGridBit(ch, step):
                        c = col
                    else:
                        c = scale(col, 0.18 if (k // 4) % 2 == 0 else 0.08)
                    if step == step_now:
                        c = WHITE
                    f[row * 10 + k + 1] = c
            # brighten the side marker for the selected channel
            f[(8 - slot * 2) * 10 + 9] = col if ch == sel else OFF
    elif mode == MODE_PLAY:
        ch = channels.selectedChannel()
        col = chan_rgb(ch) if ch < count else (0, 40, 127)
        for r in range(1, 9):
            for c in range(1, 9):
                f[r * 10 + c] = scale(col, 0.35)
    else:  # MODE_MIX
        for k in range(8):
            t = 1 + mix_page * 8 + k
            if t >= mixer.trackCount():
                continue
            c = k + 1
            f[10 + c] = (127, 0, 0) if mixer.isTrackMuted(t) else (30, 0, 0)
            f[20 + c] = (127, 100, 0) if mixer.isTrackSolo(t) else (30, 24, 0)
            vol = mixer.getTrackVolume(t)
            for r in range(3, 9):
                lit = (r - 2) / 6.0 <= vol + 0.08
                f[r * 10 + c] = (0, 110, 30) if lit else (0, 8, 3)

    # side / top buttons
    f[89] = (0, 127, 60) if mode == MODE_SEQ else (0, 20, 8)
    f[79] = (0, 80, 127) if mode == MODE_PLAY else (0, 12, 25)
    f[69] = (127, 60, 0) if mode == MODE_MIX else (25, 12, 0)
    f[19] = (0, 127, 0) if transport.isPlaying() else (0, 25, 0)
    f[29] = (127, 0, 0) if transport.isRecording() else (25, 0, 0)
    f[39] = (60, 60, 127)
    f[49] = (60, 60, 127)
    f[91] = (40, 40, 40)
    f[92] = (40, 40, 40)
    set_leds(f)


# ---------------------------------------------------------------- input
def handle_button(cc):
    global mode, chan_page, mix_page
    if cc == 91:
        if mode == MODE_MIX:
            mix_page = max(0, mix_page - 1)
        else:
            chan_page = max(0, chan_page - 1)
    elif cc == 92:
        if mode == MODE_MIX:
            if 1 + (mix_page + 1) * 8 < mixer.trackCount():
                mix_page += 1
        else:
            if (chan_page + 1) * 4 < channels.channelCount():
                chan_page += 1
    elif cc == 89:
        mode = MODE_SEQ
    elif cc == 79:
        mode = MODE_PLAY
    elif cc == 69:
        mode = MODE_MIX
    elif cc == 19:
        if transport.isPlaying():
            transport.stop()
        else:
            transport.start()
    elif cc == 29:
        transport.record()
    elif cc == 39:
        patterns.jumpToPattern(max(1, patterns.patternNumber() - 1))
    elif cc == 49:
        patterns.jumpToPattern(min(patterns.patternCount(), patterns.patternNumber() + 1))


def handle_pad(note, vel, on):
    row, col = note // 10, note % 10
    if not (1 <= row <= 8 and 1 <= col <= 8):
        return

    if mode == MODE_SEQ:
        if not on:
            return
        slot = (8 - row) // 2
        half = (8 - row) % 2
        step = half * 8 + (col - 1)
        ch = chan_page * 4 + slot
        if ch >= channels.channelCount():
            return
        channels.selectOneChannel(ch)
        channels.setGridBit(ch, step, 0 if channels.getGridBit(ch, step) else 1)

    elif mode == MODE_PLAY:
        if on:
            ch = channels.selectedChannel()
            n = min(127, 36 + (row - 1) * 5 + (col - 1))
            held[note] = (ch, n)
            channels.midiNoteOn(ch, n, max(1, vel))
        else:
            if note in held:
                ch, n = held.pop(note)
                channels.midiNoteOn(ch, n, 0)

    else:  # MODE_MIX
        if not on:
            return
        t = 1 + mix_page * 8 + (col - 1)
        if t >= mixer.trackCount():
            return
        if row == 1:
            mixer.muteTrack(t)
        elif row == 2:
            mixer.soloTrack(t)
        else:
            mixer.setTrackVolume(t, (row - 2) / 6.0)


# ---------------------------------------------------------------- FL callbacks
def OnInit():
    sysex(HDR + [0x0E, 0x01, 0xF7])   # enter Programmer mode
    last_frame.clear()
    draw()


def OnDeInit():
    last_frame.clear()
    sysex(HDR + [0x0E, 0x00, 0xF7])   # back to Live mode


def OnMidiMsg(event):
    s = event.status & 0xF0
    if s == 0xB0:                      # round buttons
        event.handled = True
        if event.data2 > 0:
            handle_button(event.data1)
    elif s in (0x90, 0x80):            # pads
        event.handled = True
        on = (s == 0x90 and event.data2 > 0)
        handle_pad(event.data1, event.data2, on)
    else:
        return
    draw()


def OnRefresh(flags):
    draw()


def OnUpdateBeatIndicator(value):
    draw()


def OnIdle():
    global last_step
    s = cur_step()
    if s != last_step:
        last_step = s
        draw()
