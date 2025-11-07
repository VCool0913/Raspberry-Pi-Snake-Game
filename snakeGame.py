# snake_16x16_gui.py (v9.1 single-game Snake: score screen + stable physical reset)
import time, random
import board, neopixel
from tkinter import (
    Tk, Button as TkButton, Toplevel, Label, Entry, Button,
    Checkbutton, BooleanVar, IntVar, Spinbox, Frame, Scale, HORIZONTAL, StringVar
)
from tkinter import ttk

# Optional physical buttons
USE_GPIO = True
try:
    from gpiozero import Button as GpioButton
except Exception:
    USE_GPIO = False

# --- LED matrix config ---
W, H = 16, 16
N = W * H
PIN = board.D24          # LED data on GPIO24
BRIGHTNESS = 0.15
ORDER = neopixel.GRBW
pixels = neopixel.NeoPixel(PIN, N, pixel_order=ORDER, auto_write=False, brightness=BRIGHTNESS)

def idx(x, y):
    # serpentine rows, origin top-left
    if y % 2 == 0: return y * W + x
    return y * W + (W - 1 - x)

def clamp255(v): return max(0, min(255, int(v)))

def wheel(pos):
    pos %= 256
    if pos < 85:   return (pos*3, 255-pos*3, 0, 0)
    if pos < 170:
        pos -= 85; return (255-pos*3, 0, pos*3, 0)
    pos -= 170;    return (0, pos*3, 255-pos*3, 0)

# --- constants/colors ---
BLACK = (0,0,0,0)
LOW_WHITE = (0,0,0,60)  # round end flash
LOW_RED   = (40,0,0,0)  # death flash

# Directions per requested mapping
UP, DOWN = (0,-1), (0,1)
LEFT, RIGHT = (1,0), (-1,0)

# --- GUI root / kiosk ---
root = Tk()
root.title("Snake")
root.geometry("520x640")
root.attributes("-fullscreen", True)   # kiosk start

TOPLEVELS = set()
def _track(win):
    TOPLEVELS.add(win)
    def _on_destroy(_e=None):
        TOPLEVELS.discard(win)
    win.bind("<Destroy>", _on_destroy, add="+")
    return win

def all_windows():
    return [root, *list(TOPLEVELS)]

def minimize_all():
    for w in all_windows():
        try: w.iconify()
        except: pass

def set_fullscreen(val: bool):
    fullscreen_var.set(bool(val))
    try:
        root.attributes("-fullscreen", bool(val))
    except Exception:
        pass

def exit_fullscreen_and_minimize():
    set_fullscreen(False)
    minimize_all()

# --- helpers ---
def flash(color, times=2, dur=0.12):
    for _ in range(times):
        pixels.fill(color); pixels.show(); time.sleep(dur)
        pixels.fill(BLACK); pixels.show(); time.sleep(dur)

# --- scoreboard + high score ---
SCORE_HOLD_SECONDS = 5
high_score = 0

# --- timer (optional rounds) ---
timed_mode = False
timed_seconds = 120
_run_started_at = None
def start_timer_if_needed():
    global _run_started_at
    if timed_mode and _run_started_at is None:
        _run_started_at = time.monotonic()
def timer_expired():
    if not timed_mode or _run_started_at is None: return False
    return (time.monotonic() - _run_started_at) >= float(timed_seconds)
def reset_timer():
    global _run_started_at
    _run_started_at = None

# --- Snake game ---
class GameSnake:
    name = "Snake"
    def __init__(self):
        # configurable colors
        self.col_snake = (0,120,0,0)
        self.col_head  = (0,200,0,0)
        self.col_food  = (120,0,0,0)
        self.col_score = (0,0,120,0)
        self.rainbow = False
        self.walls_enabled = False
        self.apples_total = 1

        # state
        self.state = "waiting_start"
        self.direction = RIGHT
        self.pending = RIGHT
        self.snake = [(3,8),(2,8),(1,8)]
        self.occ = set(self.snake)
        self.foods = set()
        self._spawn_foods()
        self.over_at = None

        # GPIO restart combo
        self.last_up_press = 0.0
        self.last_down_press = 0.0
        self._combo_armed = False  # becomes True only when both were pressed close together

    # --- admin panels (settings + colors) ---
    def admin_settings(self, parent):
        Label(parent, text="Apples on board").pack(pady=(4,2))
        apples_var = IntVar(value=self.apples_total)
        Spinbox(parent, from_=1, to=10, textvariable=apples_var, width=6).pack()
        def apply_apples():
            self.apples_total = max(1, int(apples_var.get()))
            self.foods = {p for p in self.foods if p not in self.occ}
            self._spawn_foods()
        Button(parent, text="Apply apples", command=apply_apples).pack(pady=6)

        walls_var = BooleanVar(value=self.walls_enabled)
        def on_walls(): self.walls_enabled = bool(walls_var.get())
        Checkbutton(parent, text="Enable borders (no wrap)", variable=walls_var, command=on_walls).pack(pady=6)

        rain_var = BooleanVar(value=self.rainbow)
        def on_rain(): self.rainbow = bool(rain_var.get())
        Checkbutton(parent, text="Rainbow snake", variable=rain_var, command=on_rain).pack(pady=6)

        Button(parent, text="Reset HIGH SCORE", command=reset_high_score).pack(pady=10)

    def admin_colors(self, parent):
        from tkinter.colorchooser import askcolor
        def pick_color(current, setter):
            rgb, _ = askcolor(color="#%02x%02x%02x" % current[:3], parent=parent)
            if not rgb: return
            setter((clamp255(rgb[0]), clamp255(rgb[1]), clamp255(rgb[2]), 0))
        Button(parent, text="Snake body color", command=lambda: pick_color(self.col_snake, lambda c: setattr(self, "col_snake", c))).pack(pady=6)
        Button(parent, text="Snake head color",  command=lambda: pick_color(self.col_head,  lambda c: setattr(self, "col_head",  c))).pack(pady=6)
        Button(parent, text="Apple color",       command=lambda: pick_color(self.col_food,  lambda c: setattr(self, "col_food",  c))).pack(pady=6)
        Button(parent, text="Score color",       command=lambda: pick_color(self.col_score, lambda c: setattr(self, "col_score", c))).pack(pady=6)

    # --- core game ---
    def _spawn_foods(self):
        while len(self.foods) < self.apples_total:
            p = (random.randrange(0,W), random.randrange(0,H))
            if p not in self.occ and p not in self.foods:
                self.foods.add(p)

    def score(self):
        return max(0, len(self.snake) - 3)

    def _orient_start(self, d):
        # allow LEFT/UP/DOWN at startup; ignore RIGHT
        cx, cy = 3, 8
        if d == LEFT:   s = [(cx,cy),(cx-1,cy),(cx-2,cy)]
        elif d == UP:   s = [(cx,cy),(cx,cy+1),(cx,cy+2)]
        elif d == DOWN: s = [(cx,cy),(cx,cy-1),(cx,cy-2)]
        else:           s = [(cx,cy),(cx+1,cy),(cx+2,cy)]  # RIGHT ignored elsewhere
        self.snake = [(x % W, y % H) for (x,y) in s]
        self.occ = set(self.snake)

    def on_dir_gui(self, d):
        if not touch_controls_enabled.get():
            return  # GUI input disabled while toggle OFF
        self._on_dir(d)

    def on_dir_gpio(self, d):
        # record for combo reset
        now = time.monotonic()
        if d == UP:
            self.last_up_press = now
        elif d == DOWN:
            self.last_down_press = now

        # Arm combo only when both pressed within window; clear after use
        if self._combo_detected():
            self._perform_combo_reset()
            return

        self._on_dir(d)

    def _combo_detected(self):
        if self.last_up_press > 0 and self.last_down_press > 0:
            if abs(self.last_up_press - self.last_down_press) <= 0.35:
                return True
        return False

    def _perform_combo_reset(self):
        # Reset once and clear the combo timestamps so it does not latch
        self.reset()
        # clear timestamps to prevent immediate re-trigger on next button
        self.last_up_press = 0.0
        self.last_down_press = 0.0

    def _on_dir(self, d):
        if self.state == "waiting_start":
            if d == RIGHT:
                return  # ignore RIGHT at startup
            self.pending = d
            self._orient_start(d)
            self.state = "running"
            reset_timer()  # new run
            start_timer_if_needed()
            return
        if self.state != "running":
            return
        # running
        if (self.direction[0] + d[0], self.direction[1] + d[1]) != (0,0):
            self.pending = d

    def _move(self):
        self.direction = self.pending
        hx, hy = self.snake[0]
        tx, ty = self.snake[-1]
        nx, ny = hx + self.direction[0], hy + self.direction[1]
        if self.walls_enabled:
            if nx < 0 or nx >= W or ny < 0 or ny >= H:
                return False
        else:
            nx %= W; ny %= H
        if (nx,ny) in self.occ and (nx,ny) != (tx,ty):
            return False
        ate = (nx,ny) in self.foods
        self.snake.insert(0, (nx,ny)); self.occ.add((nx,ny))
        if ate:
            self.foods.remove((nx,ny))
            self._spawn_foods()
        else:
            tail = self.snake.pop(); self.occ.discard(tail)
        return True

    def tick(self):
        if self.state == "waiting_start":
            return
        if self.state == "running":
            if timer_expired():
                self._round_end(); return
            if not self._move():
                self._death(); return
            return
        if self.state == "game_over":
            # auto-restart after hold
            if self.over_at and (time.monotonic() - self.over_at) > SCORE_HOLD_SECONDS:
                self.reset()
            return

    def _death(self):
        global high_score
        if self.score() > high_score:
            high_score = self.score()
            update_high_score_label()
        self.state = "game_over"
        self.over_at = time.monotonic()
        flash(LOW_RED, 2, 0.12)

    def _round_end(self):
        global high_score
        if self.score() > high_score:
            high_score = self.score()
            update_high_score_label()
        self.state = "game_over"
        self.over_at = time.monotonic()
        flash(LOW_WHITE, 2, 0.12)

    def draw(self):
        # LEDs draw always, independent of GUI state
        pixels.fill(BLACK)
        for fx,fy in self.foods:
            pixels[idx(fx,fy)] = self.col_food
        if self.rainbow:
            for i,(x,y) in enumerate(self.snake[1:], start=1):
                pixels[idx(x,y)] = wheel((i*12) & 255)
        else:
            for (x,y) in self.snake[1:]:
                pixels[idx(x,y)] = self.col_snake
        hx,hy = self.snake[0]
        pixels[idx(hx,hy)] = self.col_head
        pixels.show()

    def draw_score_or_status(self):
        # Show this round’s score on LEDs
        self._draw_number_centered(self.score(), self.col_score, mirror_x=True)

    def reset(self):
        # back to startup position
        self.state = "waiting_start"
        self.direction = RIGHT
        self.pending = RIGHT
        self.snake = [(3,8),(2,8),(1,8)]
        self.occ = set(self.snake)
        self.foods.clear()
        self._spawn_foods()
        self.over_at = None
        reset_timer()
        # also clear combo latch to avoid immediate re-trigger
        self.last_up_press = 0.0
        self.last_down_press = 0.0
        self.draw()

    # --- 3x5 digits with X-mirror that also reverses digit order when mirroring ---
    def _draw_digit(self, ch, ox, oy, color, mirror_x=False):
        DIGITS = {
            '0': ["111","101","101","101","111"],
            '1': ["010","110","010","010","111"],
            '2': ["111","001","111","100","111"],
            '3': ["111","001","111","001","111"],
            '4': ["101","101","111","001","001"],
            '5': ["111","100","111","001","111"],
            '6': ["111","100","111","101","111"],
            '7': ["111","001","001","010","010"],
            '8': ["111","101","111","101","111"],
            '9': ["111","101","111","001","111"],
        }
        pat = DIGITS.get(ch)
        if not pat: return
        for y, row in enumerate(pat):
            for x, c in enumerate(row):
                if c != '1': 
                    continue
                px = ox + (2 - x) if mirror_x else ox + x
                py = oy + y
                if 0 <= px < W and 0 <= py < H:
                    pixels[idx(px,py)] = color

    def _draw_number_centered(self, n, color, mirror_x=False):
        s = str(n)
        w = len(s) * 4 - 1  # 3px glyph + 1px space
        h = 5
        ox = max(0, (W - w)//2)
        oy = max(0, (H - h)//2)

        pixels.fill(BLACK)

        seq = s[::-1] if mirror_x else s
        for i, ch in enumerate(seq):
            x = ox + i * 4
            self._draw_digit(ch, x, oy, color, mirror_x=mirror_x)

        pixels.show()

# --- Instantiate game ---
game = GameSnake()

# --- Physical buttons (optional) ---
GPIO_PINS = {
    "UP": 5,
    "DOWN": 6,
    "LEFT": 13,   # per request
    "RIGHT": 19,  # per request
}
gpio_buttons = {}
def setup_gpio():
    if not USE_GPIO: return
    try:
        for name, pin in GPIO_PINS.items():
            btn = GpioButton(pin, pull_up=True, bounce_time=0.03, hold_time=0.0)
            gpio_buttons[name] = btn
        gpio_buttons["UP"].when_pressed    = lambda: game.on_dir_gpio(UP)
        gpio_buttons["DOWN"].when_pressed  = lambda: game.on_dir_gpio(DOWN)
        gpio_buttons["LEFT"].when_pressed  = lambda: game.on_dir_gpio(LEFT)
        gpio_buttons["RIGHT"].when_pressed = lambda: game.on_dir_gpio(RIGHT)
    except Exception:
        gpio_buttons.clear()

# --- Root controls (GUI D-pad) ---
btn_style = dict(height=3, width=10, font=("Arial", 16))
b_up    = TkButton(root, text="↑", **btn_style, command=lambda: game.on_dir_gui(UP))
b_down  = TkButton(root, text="↓", **btn_style, command=lambda: game.on_dir_gui(DOWN))
b_left  = TkButton(root, text="←", **btn_style, command=lambda: game.on_dir_gui(LEFT))
b_right = TkButton(root, text="→", **btn_style, command=lambda: game.on_dir_gui(RIGHT))
b_reset = TkButton(root, text="Reset", height=2, width=10, command=lambda: game.reset())

# Admin button + high-score label
def reset_high_score():
    global high_score
    high_score = 0
    update_high_score_label()

def update_high_score_label():
    hs_var.set(f"High Score: {high_score}")

b_admin = TkButton(root, text="Admin", height=1, width=10, command=lambda: open_admin())
hs_var = StringVar(value="High Score: 0")
hs_label = Label(root, textvariable=hs_var, font=("Arial", 28))

# Layout grid
for r in (0,1,2,3,4): root.grid_rowconfigure(r, weight=1)
for c in (0,1,2):     root.grid_columnconfigure(c, weight=1)

b_admin.grid(row=0, column=0, sticky="nw", padx=8, pady=8)
b_up.grid(   row=1, column=1, sticky="nsew", padx=8, pady=8)
b_left.grid( row=2, column=0, sticky="nsew", padx=8, pady=8)
b_right.grid(row=2, column=2, sticky="nsew", padx=8, pady=8)
b_down.grid( row=3, column=1, sticky="nsew", padx=8, pady=8)
b_reset.grid(row=3, column=2, sticky="nsew", padx=8, pady=8)
hs_label.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=8, pady=8)
hs_label.grid_remove()

def set_controls_enabled(enabled: bool):
    state = "normal" if enabled else "disabled"
    for w in (b_up, b_down, b_left, b_right, b_reset):
        try: w.config(state=state)
        except: pass

# Keyboard controls map to GUI player
root.bind("<Up>",    lambda e: game.on_dir_gui(UP))
root.bind("<Down>",  lambda e: game.on_dir_gui(DOWN))
root.bind("<Left>",  lambda e: game.on_dir_gui(LEFT))
root.bind("<Right>", lambda e: game.on_dir_gui(RIGHT))
# Failsafe hotkeys
root.bind("<F10>",   lambda e: open_admin())
root.bind("<F11>",   lambda e: set_fullscreen(not root.attributes("-fullscreen")))
root.bind("<Escape>",lambda e: exit_fullscreen_and_minimize())
root.focus_set()

# --- Admin Notebook (topmost, tabs) ---
admin_window = None
ADMIN_CODE = "0028"
timed_var = BooleanVar(value=False)
minutes_var = IntVar(value=2)
score_hold_var = IntVar(value=SCORE_HOLD_SECONDS)
fullscreen_var = BooleanVar(value=True)
brightness_var = None
touch_controls_enabled = BooleanVar(value=True)  # toggle D-pad visibility and input

def open_admin():
    global admin_window
    if admin_window and admin_window.winfo_exists():
        admin_window.deiconify(); admin_window.lift(); admin_window.focus_force()
        admin_window.attributes("-topmost", True)
        admin_window.after(200, lambda: admin_window.attributes("-topmost", True))
        return

    # Keypad dialog, modal and topmost
    dlg = _track(Toplevel(root))
    dlg.title("Admin")
    dlg.geometry("280x360")
    dlg.transient(root)
    dlg.attributes("-topmost", True)
    dlg.lift(); dlg.focus_force(); dlg.grab_set()

    Label(dlg, text="Enter code").pack(pady=(10,4))
    code_disp = Entry(dlg, show="*", justify="center", font=("Arial", 16))
    code_disp.pack(pady=(0,10), ipadx=6, ipady=6)
    code_disp.focus_set()

    btn_frame = Frame(dlg); btn_frame.pack()

    def press(d):
        s = code_disp.get()
        if len(s) < 8:
            code_disp.delete(0,'end'); code_disp.insert(0, s + d)
    def back():
        s = code_disp.get()
        code_disp.delete(0,'end'); code_disp.insert(0, s[:-1])
    def clear():
        code_disp.delete(0,'end')
    def submit():
        if code_disp.get() == ADMIN_CODE:
            try: dlg.grab_release()
            except: pass
            dlg.destroy()
            show_admin_window()
        else:
            clear()

    keys = [("1","2","3"),("4","5","6"),("7","8","9"),("←","0","OK")]
    for row in keys:
        fr = Frame(btn_frame); fr.pack()
        for k in row:
            if k.isdigit():
                Button(fr, text=k, width=5, height=2, command=lambda x=k: press(x)).pack(side="left", padx=6, pady=6)
            elif k == "←":
                Button(fr, text="←", width=5, height=2, command=back).pack(side="left", padx=6, pady=6)
            else:
                Button(fr, text="OK", width=5, height=2, command=submit).pack(side="left", padx=6, pady=6)

def show_admin_window():
    global admin_window, brightness_var, SCORE_HOLD_SECONDS
    if admin_window and admin_window.winfo_exists():
        admin_window.deiconify(); admin_window.lift(); admin_window.focus_force()
        admin_window.attributes("-topmost", True)
        admin_window.after(200, lambda: admin_window.attributes("-topmost", True))
        return

    # Disable D-pad while Admin open
    set_controls_enabled(False)

    admin_window = _track(Toplevel(root))
    admin_window.title("Admin Settings")
    admin_window.geometry("520x640")
    admin_window.minsize(480, 600)
    admin_window.transient(root)
    admin_window.attributes("-topmost", True)
    admin_window.lift(); admin_window.focus_force()
    admin_window.grab_set()  # modal

    def _on_close():
        try: admin_window.grab_release()
        except: pass
        try: admin_window.destroy()
        except: pass
        set_controls_enabled(touch_controls_enabled.get())

    admin_window.protocol("WM_DELETE_WINDOW", _on_close)

    notebook = ttk.Notebook(admin_window)
    notebook.pack(fill="both", expand=True, padx=6, pady=6)

    # --- Game Rules tab ---
    rules_tab = Frame(notebook)
    notebook.add(rules_tab, text="Game Rules")

    def on_toggle_timed():
        global timed_mode
        timed_mode = bool(timed_var.get()); reset_timer()
    Checkbutton(rules_tab, text="Enable rounds (timed)", variable=timed_var, command=on_toggle_timed).pack(pady=6)

    Label(rules_tab, text="Round length (minutes)").pack(pady=(4,2))
    Spinbox(rules_tab, from_=1, to=30, textvariable=minutes_var, width=6).pack()
    def apply_len():
        global timed_seconds
        timed_seconds = max(1, int(minutes_var.get())) * 60
    Button(rules_tab, text="Apply round length", command=apply_len).pack(pady=6)

    Label(rules_tab, text="Score screen hold (seconds)").pack(pady=(8,2))
    Spinbox(rules_tab, from_=1, to=90, textvariable=score_hold_var, width=6).pack()
    def apply_hold():
        global SCORE_HOLD_SECONDS
        SCORE_HOLD_SECONDS = max(1, int(score_hold_var.get()))
    Button(rules_tab, text="Apply score hold", command=apply_hold).pack(pady=6)

    Label(rules_tab, text="This game's settings").pack(pady=(12,4))
    game_settings_frame = Frame(rules_tab)
    game_settings_frame.pack(fill="both", expand=True, padx=4, pady=4)
    game.admin_settings(game_settings_frame)

    # --- Colors tab ---
    colors_tab = Frame(notebook)
    notebook.add(colors_tab, text="Colors")
    game_colors_frame = Frame(colors_tab)
    game_colors_frame.pack(fill="both", expand=True, padx=4, pady=4)
    game.admin_colors(game_colors_frame)

    # --- Screen tab ---
    screen_tab = Frame(notebook)
    notebook.add(screen_tab, text="Screen")

    fullscreen_var.set(bool(root.attributes("-fullscreen")))
    def on_toggle_fullscreen():
        set_fullscreen(fullscreen_var.get())
    Checkbutton(screen_tab, text="Fullscreen", variable=fullscreen_var, command=on_toggle_fullscreen).pack(pady=8)

    Button(screen_tab, text="Exit fullscreen + Minimize all",
           command=lambda:(exit_fullscreen_and_minimize(), _on_close())).pack(pady=10)

    Label(screen_tab, text="LED Brightness").pack(pady=(12,4))
    if brightness_var is None:
        brightness_var = IntVar(value=int(BRIGHTNESS*100))
    def on_brightness(val):
        v = max(2, min(100, int(float(val)))) / 100.0
        pixels.brightness = v
        pixels.show()
    Scale(screen_tab, from_=2, to=100, orient=HORIZONTAL, variable=brightness_var,
          command=on_brightness, length=380).pack(pady=6)

    # Touch D-pad toggle
    def on_toggle_touch():
        apply_touch_toggle()
    Checkbutton(screen_tab, text="Enable touch D-pad", variable=touch_controls_enabled, command=on_toggle_touch).pack(pady=10)

    Button(screen_tab, text="Reset HIGH SCORE", command=reset_high_score).pack(pady=6)

def apply_touch_toggle():
    # Show/hide D-pad and high score label. Admin remains available.
    on = bool(touch_controls_enabled.get())
    if on:
        # show buttons
        b_up.grid(); b_down.grid(); b_left.grid(); b_right.grid(); b_reset.grid()
        hs_label.grid_remove()
        set_controls_enabled(True)
    else:
        # hide buttons, show high score on GUI
        b_up.grid_remove(); b_down.grid_remove(); b_left.grid_remove(); b_right.grid_remove(); b_reset.grid_remove()
        update_high_score_label()
        hs_label.grid()
        set_controls_enabled(False)  # disable clicks; physical still works

# --- Main loop ---
TICK = 120
def game_tick():
    game.tick()
    if game.state == "game_over":
        game.draw_score_or_status()
    else:
        game.draw()
    root.after(TICK, game_tick)

def on_close():
    try:
        pixels.fill(BLACK); pixels.show()
    finally:
        root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)

# Start
random.seed()
setup_gpio()
apply_touch_toggle()  # set initial GUI based on toggle
game.draw()
root.after(TICK, game_tick)
try:
    root.mainloop()
finally:
    pixels.fill(BLACK); pixels.show()