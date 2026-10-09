# name=Launchpad X - FLpad Layout
# FL Studio MIDI script that gives a Novation Launchpad X an FLpad-style workflow.
# Built from the FLpad user guide v1.0 and the Launchpad X Programmer's Reference.
#
# ASSIGN THIS SCRIPT TO THE "LPX MIDI" PORT (the SECOND interface; on Windows this is
# MIDIIN2 / MIDIOUT2). Programmer mode lives on that port, not on the DAW port.
#
# Layout (Programmer mode):  pads = notes 11-88, round buttons = CC.
#
#  TOP ROW (CC 91-98):   91 Page <   92 Page >   93 CR up   94 CR down
#                        95 Sequencer 4x/2x/1x   96 Note   97 SHIFT (hold)   98 unused
#  RIGHT COLUMN (top to bottom, CC 89..19):
#                        89 Patterns   79 Channel Rack   69 Channel Mixer   59 Graph Edit
#                        49 Clear (hold)   39 Undo   29 Play   19 Record
#
#  SHIFT + Note = Scale settings        SHIFT + Channel Rack = CR Mutes
#  SHIFT + Mixer = CR Pan               SHIFT + Undo = Redo
#  SHIFT + CR up/down (in Note view) = octave up/down
#  Hold Channel Rack while in another view = Channel Rack quick select
#  Hold Clear + press a channel pad (Channel Rack view) = clear that channel's steps
#
# NOT IMPLEMENTED (no reliable scripting call known): Tempo/Swing, Pattern Length,
# Clone Loop, Quantise, Score Log, Custom Modes, pattern clone/clear, Fruity Slicer/Slicex
# layouts, Graph Edit parameters other than Velocity.

import time
import device
import channels
import patterns
import transport
import general
import midi
import utils

HDR = [0xF0, 0x00, 0x20, 0x29, 0x02, 0x0C]
OFF = (0, 0, 0)
WHITE = (127, 127, 127)
STEP_MAX = 512
DRUM_BASE = 36                 # first note of the drum (FPC-style) layout; bank B = +16
GRAPH_EDIT_ENABLED = True      # Velocity editing only; set False if it misbehaves
P_VELOCITY = getattr(midi, "pVelocity", 2)

SCALES = [
    ("Minor", [0, 2, 3, 5, 7, 8, 10]),
    ("Major", [0, 2, 4, 5, 7, 9, 11]),
    ("Dorian", [0, 2, 3, 5, 7, 9, 10]),
    ("Mixolydian", [0, 2, 4, 5, 7, 9, 10]),
    ("Phrygian", [0, 1, 3, 5, 7, 8, 10]),
    ("Harmonic Minor", [0, 2, 3, 5, 7, 8, 11]),
    ("Minor Pentatonic", [0, 3, 5, 7, 10]),
    ("Major Pentatonic", [0, 2, 4, 7, 9]),
]
WHITE_SEMIS = [0, 2, 4, 5, 7, 9, 11, 12]
BLACK_SEMIS = [None, 1, 3, None, 6, 8, 10, None]

# ------------------------------------------------------------------ state
view = "seq"            # seq note scale patterns crack mutes mixer pan graph
seq_layout = 4          # 4x / 2x / 1x
seq_page = 0
page32 = 0              # page index (32 steps) for crack / mutes / note / graph
chan_off = 0
octave = 5              # C5 = note 60 in FL
root = 0
scale_idx = 0
note_layout = 0         # 0 chromatic, 1 scale, 2 drums
shift = False
clear_held = False
crack_temp = False
crack_prev = "seq"
crack_t0 = 0.0
last_step = -1
last_frame = {}
held = set()            # pad notes currently down
held_notes = {}         # pad note -> (channel, midi note) for note-offs
ge_sel = set()          # Graph Edit selected steps


# ------------------------------------------------------------------ helpers
def sysex(data):
    device.midiOutSysex(bytes(data))


def led_index(r0, c0):
    """r0 = 0 (top) .. 7 (bottom), c0 = 0..7 -> Programmer-mode pad number."""
    return (8 - r0) * 10 + c0 + 1


def scale_rgb(c, k):
    return (int(c[0] * k), int(c[1] * k), int(c[2] * k))


def rgb7(color):
    try:
        r, g, b = utils.ColorToRGB(color)
        return (max(r >> 1, 6), max(g >> 1, 6), max(b >> 1, 6))
    except Exception:
        return (40, 40, 127)


def ch_color(ch):
    try:
        return rgb7(channels.getChannelColor(ch))
    except Exception:
        return (40, 40, 127)


def n_channels():
    return channels.channelCount()


def sel_channel():
    try:
        return max(0, channels.selectedChannel())
    except Exception:
        return 0


def cur_step():
    try:
        if not transport.isPlaying():
            return -1
        ppq = general.getRecPPQ()
        return int(transport.getSongPos(midi.SONGLENGTH_ABSTICKS) // (ppq / 4))
    except Exception:
        return -1


def send_frame(f):
    changed = [(i, c) for i, c in f.items() if last_frame.get(i) != c]
    for n in range(0, len(changed), 40):
        msg = HDR + [0x03]
        for idx, (r, g, b) in changed[n:n + 40]:
            msg += [3, idx, r, g, b]
        msg.append(0xF7)
        sysex(msg)
    last_frame.update(f)


def trigger(pad, ch, note, vel):
    held_notes[pad] = (ch, note)
    channels.midiNoteOn(ch, note, max(1, vel))


def release(pad):
    if pad in held_notes:
        ch, note = held_notes.pop(pad)
        channels.midiNoteOn(ch, note, 0)
        return True
    return False


def toggle_step(ch, step):
    if ch >= n_channels() or step >= STEP_MAX:
        return
    channels.setGridBit(ch, step, 0 if channels.getGridBit(ch, step) else 1)


def clear_channel(ch):
    for s in range(STEP_MAX):
        if channels.getGridBit(ch, s):
            channels.setGridBit(ch, s, 0)


def note_at(rr, c0):
    """Note for the bottom-half layouts. rr = 0 (bottom row) .. 3, c0 = 0..7."""
    if note_layout == 0:                                  # chromatic, two octaves
        semis = WHITE_SEMIS[c0] if rr % 2 == 0 else BLACK_SEMIS[c0]
        if semis is None:
            return None
        n = 12 * octave + 12 * (rr // 2) + semis
    elif note_layout == 1:                                # scale, four octaves
        ivs = SCALES[scale_idx][1]
        oct_add, i = divmod(c0, len(ivs))
        n = 12 * octave + root + 12 * rr + ivs[i] + 12 * oct_add
    else:                                                 # drums: two 4x4 banks
        n = DRUM_BASE + (c0 // 4) * 16 + rr * 4 + (c0 % 4)
    return n if 0 <= n <= 127 else None


def note_color(rr, c0):
    if note_at(rr, c0) is None:
        return OFF
    if note_layout == 0:
        return (30, 127, 60) if rr % 2 == 0 else (0, 45, 15)
    if note_layout == 1:
        return (127, 30, 90) if c0 % len(SCALES[scale_idx][1]) == 0 else (0, 60, 110)
    return (127, 60, 0) if c0 < 4 else (0, 90, 127)


# ------------------------------------------------------------------ drawing
def draw_steps(f, r_top, nrows, ch, first):
    if ch >= n_channels():
        return
    col = ch_color(ch)
    ph = cur_step()
    for r in range(nrows):
        for c in range(8):
            step = first + r * 8 + c
            if step >= STEP_MAX:
                continue
            if channels.getGridBit(ch, step):
                c_out = col
            else:
                c_out = scale_rgb(col, 0.16 if (c // 4) % 2 == 0 else 0.07)
            if step == ph:
                c_out = WHITE
            f[led_index(r_top + r, c)] = c_out


def draw_channel_pads(f, muted_view):
    sel = sel_channel()
    count = n_channels()
    for i in range(32):
        ch = chan_off + i
        if ch >= count:
            break
        col = ch_color(ch)
        if muted_view:
            c = scale_rgb(col, 0.1) if channels.isChannelMuted(ch) else col
        else:
            c = WHITE if ch == sel else scale_rgb(col, 0.45)
        f[led_index(4 + i // 8, i % 8)] = c


def draw_view(f):
    v = view
    if v == "seq":
        rpc = 8 // seq_layout
        for slot in range(seq_layout):
            draw_steps(f, slot * rpc, rpc, chan_off + slot, seq_page * rpc * 8)
    elif v in ("crack", "mutes"):
        draw_steps(f, 0, 4, sel_channel(), page32 * 32)
        draw_channel_pads(f, v == "mutes")
    elif v == "note":
        draw_steps(f, 0, 4, sel_channel(), page32 * 32)
        for rb in range(4):
            for c0 in range(8):
                f[led_index(4 + rb, c0)] = note_color(3 - rb, c0)
    elif v == "scale":
        ivs = SCALES[scale_idx][1]
        for c0 in range(8):
            for r0, keys in ((2, BLACK_SEMIS), (3, WHITE_SEMIS)):
                s = keys[c0]
                if s is None:
                    continue
                s %= 12
                if s == root:
                    col = (127, 30, 90)
                elif (s - root) % 12 in ivs:
                    col = (0, 70, 127)
                else:
                    col = (30, 30, 30)
                f[led_index(r0, c0)] = col
            if c0 < len(SCALES):
                f[led_index(6, c0)] = WHITE if c0 == scale_idx else (127, 55, 0)
    elif v == "patterns":
        cnt = patterns.patternCount()
        act = patterns.patternNumber()
        for i in range(64):
            n = i + 1
            if n == act:
                c = WHITE
            elif n <= cnt:
                try:
                    c = rgb7(patterns.getPatternColor(n))
                except Exception:
                    c = (60, 60, 127)
            else:
                continue
            f[led_index(i // 8, i % 8)] = c
    elif v == "mixer":
        count = n_channels()
        for c0 in range(8):
            ch = chan_off + c0
            if ch >= count:
                continue
            lit = int(round(channels.getChannelVolume(ch) * 8))
            col = ch_color(ch)
            for r0 in range(8):
                f[led_index(r0, c0)] = col if (8 - r0) <= lit else scale_rgb(col, 0.07)
    elif v == "pan":
        count = n_channels()
        for r0 in range(8):
            ch = chan_off + r0
            if ch >= count:
                continue
            p = channels.getChannelPan(ch)
            col = ch_color(ch)
            if abs(p) < 0.08:
                lit = {3, 4}
            else:
                pos = int(round((p + 1) * 3.5))
                lit = set(range(pos, 4)) if p < 0 else set(range(4, pos + 1))
            for c0 in range(8):
                f[led_index(r0, c0)] = col if c0 in lit else scale_rgb(col, 0.07)
    elif v == "graph":
        sel = sel_channel()
        col = ch_color(sel)
        first = page32 * 32
        for r in range(4):
            for c in range(8):
                step = first + r * 8 + c
                if step in ge_sel:
                    c_out = WHITE
                elif channels.getGridBit(sel, step):
                    c_out = scale_rgb(col, 0.5)
                else:
                    c_out = scale_rgb(col, 0.08)
                f[led_index(r, c)] = c_out
        for i in range(16):
            f[led_index(5 + i // 8, i % 8)] = scale_rgb((127, 70, 0), 0.25 + 0.75 * (i + 1) / 16)
        f[led_index(7, 1)] = (127, 80, 0)          # Velocity is the only supported parameter
        for c0 in (0, 2, 3, 4, 5, 6, 7):
            f[led_index(7, c0)] = (20, 20, 20)


def draw_buttons(f):
    if view == "seq":
        size = seq_layout and (8 // seq_layout) * 8
        page = seq_page
    else:
        size = 32
        page = page32
    f[91] = (60, 60, 60) if page > 0 else (8, 8, 8)
    f[92] = (60, 60, 60) if (page + 1) * size < STEP_MAX else (8, 8, 8)
    f[93] = (60, 40, 0)
    f[94] = (60, 40, 0)
    f[95] = (0, 127, 60) if view == "seq" else (0, 20, 10)
    f[96] = (0, 110, 127) if view in ("note", "scale") else (0, 18, 25)
    f[97] = WHITE if shift else (30, 30, 30)
    f[98] = OFF
    f[89] = (127, 80, 0) if view == "patterns" else (25, 15, 0)
    f[79] = (127, 40, 100) if view in ("crack", "mutes") else (25, 8, 20)
    f[69] = (0, 100, 127) if view in ("mixer", "pan") else (0, 20, 25)
    f[59] = (100, 127, 0) if view == "graph" else (20, 25, 0)
    f[49] = (127, 0, 0) if clear_held else (40, 0, 0)
    f[39] = (80, 80, 0)
    f[29] = (0, 127, 0) if transport.isPlaying() else (0, 30, 0)
    f[19] = (127, 0, 0) if transport.isRecording() else (50, 0, 0)
    f[99] = (20, 20, 20)


def draw():
    f = {}
    for r0 in range(8):
        for c0 in range(8):
            f[led_index(r0, c0)] = OFF
    draw_view(f)
    draw_buttons(f)
    send_frame(f)


# ------------------------------------------------------------------ input: buttons
def set_view(v):
    global view, crack_temp
    view = v
    crack_temp = False
    ge_sel.clear()


def page_move(d):
    global seq_page, page32
    if view == "seq":
        size = (8 // seq_layout) * 8
        seq_page = max(0, min(seq_page + d, STEP_MAX // size - 1))
    else:
        page32 = max(0, min(page32 + d, STEP_MAX // 32 - 1))


def scroll(d):
    """d = -1 for CR up, +1 for CR down."""
    global chan_off, octave
    if shift and view in ("note", "scale"):
        octave = max(0, min(9, octave - d))
        return
    count = n_channels()
    if view in ("note", "graph"):
        channels.selectOneChannel(max(0, min(count - 1, sel_channel() + d)))
        ge_sel.clear()
        return
    step = seq_layout if view == "seq" else 8
    chan_off = max(0, min(chan_off + d * step, max(0, count - 1)))


def crack_button(down):
    global crack_temp, crack_prev, crack_t0
    if down:
        crack_t0 = time.time()
        if shift:
            set_view("mutes")
        elif view != "crack":
            crack_prev = view
            set_view("crack")
            crack_temp = True
    elif crack_temp:
        crack_temp = False
        if time.time() - crack_t0 > 0.4:      # held = quick select, tap = stay in view
            set_view(crack_prev)


def do_undo(redo):
    try:
        if redo:
            general.undoDown()
        else:
            general.undoUp()
    except Exception:
        general.undo()


def on_button(cc, val):
    global shift, clear_held, seq_layout, seq_page, note_layout
    down = val > 0
    if cc == 97:
        shift = down
        return
    if cc == 49:
        clear_held = down
        return
    if cc == 79:
        crack_button(down)
        return
    if not down:
        return
    if cc == 91:
        page_move(-1)
    elif cc == 92:
        page_move(1)
    elif cc == 93:
        scroll(-1)
    elif cc == 94:
        scroll(1)
    elif cc == 95:
        if view == "seq":
            seq_layout = {4: 2, 2: 1, 1: 4}[seq_layout]
            seq_page = 0
        else:
            set_view("seq")
    elif cc == 96:
        if shift:
            set_view("note" if view == "scale" else "scale")
        elif view == "note":
            note_layout = (note_layout + 1) % 3
        else:
            set_view("note")
    elif cc == 89:
        set_view("patterns")
    elif cc == 69:
        set_view("pan" if shift else "mixer")
    elif cc == 59:
        if GRAPH_EDIT_ENABLED:
            set_view("graph")
    elif cc == 39:
        do_undo(shift)
    elif cc == 29:
        if transport.isPlaying():
            transport.stop()
        else:
            transport.start()
    elif cc == 19:
        transport.record()


# ------------------------------------------------------------------ input: pads
def on_pad(n, vel, on):
    global root, scale_idx
    row, col = n // 10, n % 10
    if not (1 <= row <= 8 and 1 <= col <= 8):
        return
    r0, c0 = 8 - row, col - 1
    if not on:
        held.discard(n)
        release(n)
        return
    held.add(n)
    v = view

    if v == "seq":
        rpc = 8 // seq_layout
        slot = r0 // rpc
        ch = chan_off + slot
        if ch < n_channels():
            channels.selectOneChannel(ch)
            toggle_step(ch, seq_page * rpc * 8 + (r0 % rpc) * 8 + c0)

    elif v in ("crack", "mutes", "note"):
        if r0 < 4:
            toggle_step(sel_channel(), page32 * 32 + r0 * 8 + c0)
        elif v == "note":
            note = note_at(7 - r0, c0)
            if note is not None:
                trigger(n, sel_channel(), note, vel)
        else:
            ch = chan_off + (r0 - 4) * 8 + c0
            if ch >= n_channels():
                return
            if v == "mutes":
                channels.muteChannel(ch)
            elif clear_held:
                clear_channel(ch)
            else:
                channels.selectOneChannel(ch)
                trigger(n, ch, 60, vel)

    elif v == "scale":
        if r0 == 2 and BLACK_SEMIS[c0] is not None:
            root = BLACK_SEMIS[c0] % 12
        elif r0 == 3:
            root = WHITE_SEMIS[c0] % 12
        elif r0 == 6 and c0 < len(SCALES):
            scale_idx = c0

    elif v == "patterns":
        patterns.jumpToPattern(r0 * 8 + c0 + 1)

    elif v == "mixer":
        ch = chan_off + c0
        if ch < n_channels():
            channels.setChannelVolume(ch, (8 - r0) / 8.0)

    elif v == "pan":
        ch = chan_off + r0
        if ch < n_channels():
            other = led_index(r0, 7 - c0)
            if c0 in (3, 4) and other in held:      # both centre pads = reset
                channels.setChannelPan(ch, 0.0)
            else:
                channels.setChannelPan(ch, (c0 - 3.5) / 3.5)

    elif v == "graph":
        sel = sel_channel()
        if r0 < 4:
            step = page32 * 32 + r0 * 8 + c0
            if channels.getGridBit(sel, step):
                if step in ge_sel:
                    ge_sel.discard(step)
                else:
                    ge_sel.add(step)
        elif r0 in (5, 6):
            val = 7 + 8 * ((r0 - 5) * 8 + c0)        # 7 .. 127, as in the FLpad guide
            pat = patterns.patternNumber()
            for s in ge_sel:
                try:
                    channels.setStepParameterByIndex(sel, pat, s, P_VELOCITY, val)
                except Exception as e:
                    print("Graph Edit velocity failed:", e)
                    break


# ------------------------------------------------------------------ FL Studio callbacks
def OnInit():
    sysex(HDR + [0x0E, 0x01, 0xF7])        # Programmer mode
    last_frame.clear()
    draw()


def OnDeInit():
    for idx in list(last_frame.keys()):
        last_frame[idx] = None
    sysex(HDR + [0x0E, 0x00, 0xF7])        # back to Live mode


def OnMidiMsg(event):
    s = event.status & 0xF0
    if s == 0xB0:
        event.handled = True
        on_button(event.data1, event.data2)
    elif s in (0x90, 0x80):
        event.handled = True
        on_pad(event.data1, event.data2, s == 0x90 and event.data2 > 0)
    elif s in (0xA0, 0xD0):                # swallow aftertouch
        event.handled = True
        return
    else:
        return
    draw()


def OnRefresh(flags):
    draw()


def OnIdle():
    global last_step
    s = cur_step()
    if s != last_step:
        last_step = s
        draw()
