import math
import os
import random
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog

import numpy as np
import pygame
import pygame.surfarray
from pydub import AudioSegment

try:
    import pyaudiowpatch as pyaudio

    HAS_PYAUDIO = True
except ImportError:
    try:
        import pyaudio

        HAS_PYAUDIO = True
    except ImportError:
        HAS_PYAUDIO = False

AUDIO_EXTS = (".mp3", ".wav", ".flac", ".ogg")
REF_FPS = 120.0  # 所有「每幀」的平滑係數都以 120FPS 為基準，再依 dt 換算
FONT_CJK = "Microsoft JhengHei,Noto Sans CJK TC,PingFang TC,Arial Unicode MS"


def natural_key(text):
    """自然排序：track2 排在 track10 前面。"""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


class MusiCanApp:

    def __init__(self):
        pygame.init()
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)

        self.WIDTH, self.HEIGHT = 1920, 1080
        self.MAX_SIDEBAR_W = 350
        self.sidebar_w = self.MAX_SIDEBAR_W
        self.sidebar_target_w = self.MAX_SIDEBAR_W
        self._sidebar_f = float(self.MAX_SIDEBAR_W)

        self.FPS = 120
        self.BAR_COUNT = 130
        self.B_WIDTH = 6  # 柱寬
        self.B_GAP = 9  # 柱間距（130 根 x 15px = 1950px，略大於視窗寬，所以會貼齊兩邊）
        self.FFT_SIZE = 2048
        self.AUDIO_DELAY_OFFSET = 120
        self.FILE_RATE = 44100
        self.MIN_HZ = 42.0
        self.MAX_HZ = 15500.0

        self.running = True
        self.fullscreen = False

        # SCALED + RESIZABLE：內部固定 1920x1080，視窗可縮放、可全螢幕，滑鼠座標自動換算
        flags = pygame.SCALED | pygame.RESIZABLE
        try:
            self.screen = pygame.display.set_mode(
                (self.WIDTH, self.HEIGHT), flags, vsync=1
            )
        except (TypeError, pygame.error):
            self.screen = pygame.display.set_mode(
                (self.WIDTH, self.HEIGHT), flags
            )

        pygame.display.set_caption("MusiCan Studio - Perfect Web-Audio Engine")
        self.clock = pygame.time.Clock()

        # Fonts（多個備用字型，非 Windows 也能顯示中文）
        self.font_title = pygame.font.SysFont(FONT_CJK, 65, bold=True)
        self.font_ui = pygame.font.SysFont(FONT_CJK, 18)
        self.font_time = pygame.font.SysFont("monospace", 18)
        self.font_btn = pygame.font.SysFont("monospace", 22, bold=True)
        self._text_cache = {}
        self._fit_cache = {}

        # 主題顏色
        self.THEMES = {
            "RED": (255, 60, 80),
            "GREEN": (0, 255, 128),
            "BLUE": (0, 180, 255),
            "WHITE": (240, 240, 240),
        }
        self.current_theme_key = "BLUE"
        self.target_color = np.array(
            self.THEMES[self.current_theme_key], dtype=np.float32
        )
        self.current_color = np.array(
            self.THEMES[self.current_theme_key], dtype=np.float32
        )

        self.BG_DARK = (10, 0, 24)
        self.SIDEBAR_BG = (18, 5, 38)
        self.WHITE = (255, 255, 255)

        # UI 狀態與版面（所有 Rect 只在這裡定義一次，事件與繪圖共用）
        self.show_settings = False
        self.setting_btn_rect = pygame.Rect(self.WIDTH - 60, 20, 40, 40)
        self.toggle_btn_rect = pygame.Rect(self.sidebar_w, 20, 35, 45)
        self._build_settings_layout()
        self.audio_mode = "FILE"
        self.dragging_seek = False
        self.dragging_vol = False
        self.seek_pct = 0.0

        # System audio
        self.pa = None
        self.sys_stream = None
        self.sys_audio_buffer = np.zeros(self.FFT_SIZE, dtype=np.float32)
        self.sys_rate = 44100
        self.sys_gen = 0
        self.is_sys_listening = False

        # File playback
        self.playlist = []
        self.current_idx = -1
        self.song_name = "WAITING..."
        self.np_audio_data = np.array([], dtype=np.float32)
        self.audio_duration_ms = 0
        self.is_loading = False
        self.dialog_open = False
        self.load_gen = 0
        self._load_lock = threading.Lock()
        self.track_active = False  # 目前有一首歌載入並且在播放/暫停中
        self.paused = False
        self.play_offset_ms = 0  # 拖拉進度後的起點（get_pos 不含 start 位移）
        self.last_pos_ms = 0
        self.track_start_tick = 0
        self.volume = 1.0

        # FFT / 動畫 buffer
        self.fft_smoothed = np.zeros(self.BAR_COUNT)
        self.smoothed_values = np.zeros(self.BAR_COUNT)
        self.peaks = np.zeros(self.BAR_COUNT)
        self.osc_smoothed = np.zeros(self.FFT_SIZE, dtype=np.float32)
        self.shake_amount = 0.0
        self.scroll_y = 0

        # 預先計算（原本每幀都在重算）
        self.window = (np.hanning(self.FFT_SIZE) * 2.0).astype(np.float32)
        self._zeros = np.zeros(self.FFT_SIZE, dtype=np.float32)
        self._bin_axis = np.arange(self.FFT_SIZE // 2 + 1)
        # 每根柱子的固定參數（電腦版的視覺數值，原本在迴圈裡每幀重算）
        self.pct = np.arange(self.BAR_COUNT) / (self.BAR_COUNT - 1)
        self.stc = 0.55 - self.pct * 0.15
        self.power_exp = 1.4 - self.pct * 0.2
        self.height_mult = 780.0 - self.pct * 180.0
        self.fall_speed = 38.0 + self.pct * 30.0
        self._grad_cache = {}
        self._grad_color = None
        self._alpha_cache = {}
        self._band_rate = None
        self._build_band_map(self.FILE_RATE)
        step = max(1, self.FFT_SIZE // 180)
        self.osc_idx = np.arange(0, self.FFT_SIZE, step)
        self.osc_xfrac = self.osc_idx / self.FFT_SIZE

    # ------------------------------------------------------------------ 版面
    def _build_settings_layout(self):
        self.panel_rect = pygame.Rect(self.WIDTH - 280, 70, 260, 190)
        p = self.panel_rect
        self.theme_rects = {
            key: pygame.Rect(p.x + 20 + idx * 55, p.y + 40, 45, 30)
            for idx, key in enumerate(self.THEMES)
        }
        self.sys_btn_rect = pygame.Rect(p.x + 20, p.y + 88, 220, 35)
        self.fs_btn_rect = pygame.Rect(p.x + 20, p.y + 133, 220, 35)

    def transport_rects(self):
        x0 = self.sidebar_w + 80
        y = 190
        vol = pygame.Rect(x0 + 200, y + 19, 180, 8)
        return {
            "prev": pygame.Rect(x0, y, 46, 46),
            "play": pygame.Rect(x0 + 56, y, 46, 46),
            "next": pygame.Rect(x0 + 112, y, 46, 46),
            "vol": vol,
            "vol_hit": vol.inflate(20, 28),
        }

    def progress_rect(self):
        w = self.WIDTH - self.sidebar_w
        return pygame.Rect(
            self.sidebar_w + int(w * 0.05), 40, int(w * 0.90), 8
        )

    def item_rect(self, i):
        return pygame.Rect(
            10, 70 + self.scroll_y + i * 35, self.MAX_SIDEBAR_W - 20, 30
        )

    @property
    def theme_color(self):
        return (
            int(self.current_color[0]),
            int(self.current_color[1]),
            int(self.current_color[2]),
        )

    # ------------------------------------------------------------------ 文字工具
    def render_text(self, font, text, color):
        key = (id(font), text, color)
        surf = self._text_cache.get(key)
        if surf is None:
            if len(self._text_cache) > 400:
                self._text_cache.clear()
            surf = font.render(text, True, color)
            self._text_cache[key] = surf
        return surf

    def fit_text(self, font, text, max_w):
        """依實際像素寬度截斷（中日文不會爆版）。"""
        key = (id(font), text, max_w)
        cached = self._fit_cache.get(key)
        if cached is not None:
            return cached
        if font.size(text)[0] <= max_w:
            result = text
        else:
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.size(text[:mid] + "...")[0] <= max_w:
                    lo = mid
                else:
                    hi = mid - 1
            result = text[:lo] + "..."
        if len(self._fit_cache) > 800:
            self._fit_cache.clear()
        self._fit_cache[key] = result
        return result

    @staticmethod
    def format_time(ms):
        ms = int(ms)
        if ms < 0:
            ms = 0
        s = ms // 1000
        return f"{s // 60}:{s % 60:02d}"

    # ------------------------------------------------------------------ 頻譜對應
    def _build_band_map(self, rate):
        """每根柱子的目標頻率 -> FFT bin（含小數），預先算好，只有取樣率變動才重算。"""
        top = self.FFT_SIZE // 2
        hz = self.MIN_HZ * (self.MAX_HZ / self.MIN_HZ) ** self.pct
        bin_idx = hz * self.FFT_SIZE / rate
        idx0 = np.clip(np.floor(bin_idx), 0, top).astype(int)
        idx1 = np.clip(np.ceil(bin_idx), 0, top).astype(int)
        t = np.clip(bin_idx - idx0, 0.0, 1.0)
        self._band_map = (idx0, idx1, t)
        self._band_rate = rate

    def _band_amplitudes(self, fft_abs, rate):
        if rate != self._band_rate:  # 系統音訊的取樣率可能是 48000
            self._build_band_map(rate)
        idx0, idx1, t = self._band_map
        return fft_abs[idx0] * (1.0 - t) + fft_abs[idx1] * t

    # ------------------------------------------------------------------ 系統音訊
    def toggle_system_audio(self):
        if not HAS_PYAUDIO:
            print("pip install pyaudiowpatch")
            return

        if self.audio_mode == "SYSTEM":
            self._stop_system_audio()
            self.audio_mode = "FILE"
            self.song_name = "WAITING..."
        else:
            pygame.mixer.music.stop()
            self.track_active = False
            self.paused = False
            self.load_gen += 1  # 取消還在載入的歌
            self.is_loading = False
            self.audio_mode = "SYSTEM"
            self.song_name = "SYSTEM AUDIO LOOPBACK"
            self.sidebar_target_w = 0
            self.is_sys_listening = True
            self.sys_gen += 1
            threading.Thread(
                target=self._start_pyaudio_loopback,
                args=(self.sys_gen,),
                daemon=True,
            ).start()

    def _stop_system_audio(self):
        self.is_sys_listening = False
        self.sys_gen += 1  # 讓舊執行緒自己結束
        self.sys_audio_buffer = np.zeros(self.FFT_SIZE, dtype=np.float32)

    def _start_pyaudio_loopback(self, gen):
        pa_instance = None
        stream_instance = None
        try:
            pa_instance = pyaudio.PyAudio()
            self.pa = pa_instance

            try:
                loopback_device = pa_instance.get_default_wasapi_loopback()
            except AttributeError:
                wasapi_info = pa_instance.get_host_api_info_by_type(
                    pyaudio.paWASAPI
                )
                default_speakers = pa_instance.get_device_info_by_index(
                    wasapi_info["defaultOutputDevice"]
                )

                loopback_device = None
                for i in range(pa_instance.get_device_count()):
                    dev = pa_instance.get_device_info_by_index(i)
                    if (
                        dev.get("isLoopbackDevice")
                        and default_speakers["name"] in dev["name"]
                    ):
                        loopback_device = dev
                        break

                if not loopback_device:
                    raise RuntimeError("找不到預設的 Loopback 音訊裝置")

            rate = int(loopback_device["defaultSampleRate"])
            channels = max(1, int(loopback_device["maxInputChannels"]))
            self.sys_rate = rate  # 頻率對應要用裝置真正的取樣率（常見是 48000）

            stream_instance = pa_instance.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=loopback_device["index"],
                frames_per_buffer=self.FFT_SIZE,
            )
            self.sys_stream = stream_instance

            while self.is_sys_listening and gen == self.sys_gen:
                try:
                    data = stream_instance.read(
                        self.FFT_SIZE, exception_on_overflow=False
                    )
                except Exception:
                    break

                if not self.is_sys_listening or gen != self.sys_gen:
                    break

                samples = (
                    np.frombuffer(data, dtype=np.int16).astype(np.float32)
                    / 32768.0
                )

                # 自動增益（刻意保留）
                max_amp = np.max(np.abs(samples)) if samples.size else 0.0
                if max_amp > 0.001:
                    gain = min(15.0, 0.85 / max_amp)
                    samples = samples * gain

                # 所有聲道取平均，而不是只取左聲道
                if channels >= 2:
                    n = (len(samples) // channels) * channels
                    mono = samples[:n].reshape(-1, channels).mean(axis=1)
                else:
                    mono = samples

                if len(mono) < self.FFT_SIZE:
                    mono = np.pad(
                        mono, (0, self.FFT_SIZE - len(mono)), "constant"
                    )
                else:
                    mono = mono[: self.FFT_SIZE]

                self.sys_audio_buffer = mono.astype(np.float32)

        except Exception as e:
            print(f"System Audio Error: {e}")
            if gen == self.sys_gen and self.audio_mode == "SYSTEM":
                self.audio_mode = "FILE"
                self.is_sys_listening = False
                self.song_name = "WAITING..."  # 失敗時把標題一併還原
        finally:
            if stream_instance:
                try:
                    stream_instance.stop_stream()
                    stream_instance.close()
                except Exception:
                    pass
            if pa_instance:
                try:
                    pa_instance.terminate()
                except Exception:
                    pass
            self.sys_stream = None
            self.pa = None

    # ------------------------------------------------------------------ 檔案播放
    def load_folder(self):
        """檔案對話框放到背景執行緒，避免整個視窗卡住。"""
        if self.dialog_open:
            return
        self.dialog_open = True
        threading.Thread(target=self._load_folder_worker, daemon=True).start()

    def _load_folder_worker(self):
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                folder = filedialog.askdirectory(
                    title="Select Music Folder", parent=root
                )
            finally:
                root.destroy()  # 原本沒有 destroy，會一直累積 Tk 視窗
            if folder:
                files = [
                    os.path.join(folder, f)
                    for f in os.listdir(folder)
                    if f.lower().endswith(AUDIO_EXTS)
                ]
                files.sort(key=lambda p: natural_key(os.path.basename(p)))
                self.playlist = files
                self.current_idx = -1
                self.scroll_y = 0
        except Exception as e:
            print(f"Folder dialog error: {e}")
        finally:
            self.dialog_open = False

    @staticmethod
    def _decode_audio(path):
        if path.lower().endswith(".wav"):
            try:
                audio = AudioSegment.from_wav(path)
            except Exception:
                audio = AudioSegment.from_file(path)
        else:
            audio = AudioSegment.from_file(path)
        duration = len(audio)
        # 統一成 16-bit，避免 24-bit 等格式的縮放錯誤
        audio = audio.set_channels(1).set_frame_rate(44100).set_sample_width(2)
        data = np.array(audio.get_array_of_samples(), dtype=np.float32) / 32768.0
        return data, duration

    def _async_load_and_play(self, path, gen):
        try:
            data, duration = self._decode_audio(path)
            with self._load_lock:
                if gen != self.load_gen:  # 已經有更新的點歌請求
                    return
                self.np_audio_data = data
                self.audio_duration_ms = duration
                self.fft_smoothed.fill(0)
                self.smoothed_values.fill(0)
                self.peaks.fill(0)
                self.osc_smoothed.fill(0)

                pygame.mixer.music.load(path)
                pygame.mixer.music.set_volume(self.volume)
                pygame.mixer.music.play()
                self.play_offset_ms = 0
                self.last_pos_ms = 0
                self.paused = False
                self.track_start_tick = pygame.time.get_ticks()
                self.track_active = True
        except Exception as e:
            print(f"Error loading audio: {e}")
            if gen == self.load_gen:
                self.song_name = "LOAD ERROR"
        finally:
            if gen == self.load_gen:
                self.is_loading = False

    def play_song(self, idx):
        if idx < 0 or idx >= len(self.playlist):
            return

        if self.audio_mode == "SYSTEM":
            self._stop_system_audio()

        self.audio_mode = "FILE"
        self.load_gen += 1
        gen = self.load_gen
        self.is_loading = True
        self.track_active = False
        self.paused = False
        pygame.mixer.music.stop()
        self.current_idx = idx
        path = self.playlist[idx]
        self.song_name = os.path.splitext(os.path.basename(path))[0].upper()
        self.sidebar_target_w = 0

        threading.Thread(
            target=self._async_load_and_play, args=(path, gen), daemon=True
        ).start()

    def next_song(self):
        if self.playlist:
            self.play_song((self.current_idx + 1) % len(self.playlist))

    def prev_song(self):
        if not self.playlist:
            return
        if self.track_active and self.get_position_ms() > 3000:
            self.seek_to(0)  # 播超過 3 秒就先回到開頭
            return
        if self.current_idx < 0:
            self.play_song(0)
        else:
            self.play_song((self.current_idx - 1) % len(self.playlist))

    def toggle_pause(self):
        if self.audio_mode != "FILE" or not self.track_active:
            return
        if self.paused:
            pygame.mixer.music.unpause()
        else:
            pygame.mixer.music.pause()
        self.paused = not self.paused

    def get_position_ms(self):
        if not self.track_active:
            return 0
        p = pygame.mixer.music.get_pos()
        if p >= 0:
            self.last_pos_ms = self.play_offset_ms + p
        return self.last_pos_ms

    def seek_to(self, ms):
        if not self.track_active or self.audio_duration_ms <= 0:
            return
        ms = int(max(0, min(ms, self.audio_duration_ms - 300)))
        try:
            pygame.mixer.music.play(start=ms / 1000.0)
        except pygame.error:
            # 此格式不支援跳轉（部分 wav）：退回從頭播放
            ms = 0
            try:
                pygame.mixer.music.play()
            except pygame.error:
                return
        pygame.mixer.music.set_volume(self.volume)
        self.play_offset_ms = ms
        self.last_pos_ms = ms
        self.track_start_tick = pygame.time.get_ticks()
        if self.paused:
            pygame.mixer.music.pause()

    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, v))
        pygame.mixer.music.set_volume(self.volume)

    def toggle_fullscreen(self):
        try:
            pygame.display.toggle_fullscreen()
            self.fullscreen = not self.fullscreen
        except pygame.error as e:
            print(f"Fullscreen error: {e}")
        self.screen = pygame.display.get_surface()

    def check_track_end(self):
        """歌曲播完自動下一首。"""
        if (
            self.audio_mode == "FILE"
            and self.track_active
            and not self.paused
            and not self.is_loading
            and pygame.time.get_ticks() - self.track_start_tick > 800
            and not pygame.mixer.music.get_busy()
        ):
            self.track_active = False
            self.next_song()

    # ------------------------------------------------------------------ 事件
    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                self._on_key(event.key)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self._on_click(event.pos)
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self._on_release()
            elif event.type == pygame.MOUSEMOTION:
                self._on_drag(event.pos)
            elif event.type == pygame.MOUSEWHEEL:
                self._on_wheel(event.y)

    def _on_key(self, key):
        if key == pygame.K_ESCAPE:
            if self.fullscreen:
                self.toggle_fullscreen()
            else:
                self.running = False
        elif key in (pygame.K_F11, pygame.K_f):
            self.toggle_fullscreen()
        elif self.audio_mode == "FILE":
            if key == pygame.K_SPACE:
                self.toggle_pause()
            elif key == pygame.K_RIGHT:
                self.seek_to(self.get_position_ms() + 5000)
            elif key == pygame.K_LEFT:
                self.seek_to(self.get_position_ms() - 5000)
            elif key == pygame.K_UP:
                self.set_volume(self.volume + 0.05)
            elif key == pygame.K_DOWN:
                self.set_volume(self.volume - 0.05)
            elif key == pygame.K_n:
                self.next_song()
            elif key == pygame.K_p:
                self.prev_song()

    def _pct_on(self, rect, mx):
        return max(0.0, min(1.0, (mx - rect.x) / max(1, rect.w)))

    def _on_click(self, pos):
        mx, my = pos

        if self.setting_btn_rect.collidepoint(pos):
            self.show_settings = not self.show_settings
            return

        if self.show_settings:
            if self.panel_rect.collidepoint(pos):
                for key, rect in self.theme_rects.items():
                    if rect.collidepoint(pos):
                        self.current_theme_key = key
                        self.target_color = np.array(
                            self.THEMES[key], dtype=np.float32
                        )
                if self.sys_btn_rect.collidepoint(pos):
                    self.toggle_system_audio()
                elif self.fs_btn_rect.collidepoint(pos):
                    self.toggle_fullscreen()
                return
            self.show_settings = False

        if self.audio_mode == "FILE":
            if self.toggle_btn_rect.collidepoint(pos):
                self.sidebar_target_w = (
                    0
                    if self.sidebar_target_w == self.MAX_SIDEBAR_W
                    else self.MAX_SIDEBAR_W
                )
                return

        if (
            self.sidebar_target_w == self.MAX_SIDEBAR_W
            and mx < self.MAX_SIDEBAR_W
        ):
            btn_rect = pygame.Rect(10, 10, self.MAX_SIDEBAR_W - 20, 40)
            if btn_rect.collidepoint(pos):
                self.load_folder()
            elif my > 60:
                i = (my - 70 - self.scroll_y) // 35
                if 0 <= i < len(self.playlist) and self.item_rect(i).collidepoint(pos):
                    self.play_song(i)
            return

        if self.audio_mode == "FILE":
            tr = self.transport_rects()
            if tr["prev"].collidepoint(pos):
                self.prev_song()
            elif tr["play"].collidepoint(pos):
                self.toggle_pause()
            elif tr["next"].collidepoint(pos):
                self.next_song()
            elif tr["vol_hit"].collidepoint(pos):
                self.dragging_vol = True
                self.set_volume(self._pct_on(tr["vol"], mx))
            elif self.track_active and self.progress_rect().inflate(0, 24).collidepoint(pos):
                self.dragging_seek = True
                self.seek_pct = self._pct_on(self.progress_rect(), mx)

    def _on_release(self):
        if self.dragging_seek:
            self.dragging_seek = False
            self.seek_to(self.seek_pct * self.audio_duration_ms)
        self.dragging_vol = False

    def _on_drag(self, pos):
        if self.dragging_seek:
            self.seek_pct = self._pct_on(self.progress_rect(), pos[0])
        if self.dragging_vol:
            self.set_volume(self._pct_on(self.transport_rects()["vol"], pos[0]))

    def _on_wheel(self, dy):
        mx, _ = pygame.mouse.get_pos()
        if self.sidebar_target_w == self.MAX_SIDEBAR_W and mx < self.MAX_SIDEBAR_W:
            max_scroll = -max(0, len(self.playlist) * 35 - (self.HEIGHT - 80))
            self.scroll_y = max(max_scroll, min(0, self.scroll_y + dy * 35))

    # ------------------------------------------------------------------ 更新
    def update_animation(self, k):
        """k = dt * REF_FPS，讓動畫速度不受實際 FPS 影響。"""
        self._sidebar_f += (self.sidebar_target_w - self._sidebar_f) * (
            1.0 - 0.8 ** k
        )
        if abs(self._sidebar_f - self.sidebar_target_w) < 2:
            self._sidebar_f = float(self.sidebar_target_w)
        self.sidebar_w = int(round(self._sidebar_f))
        self.toggle_btn_rect.x = self.sidebar_w + 5

        self.current_color += (self.target_color - self.current_color) * (
            1.0 - 0.9 ** k
        )

    def update_audio(self, k):
        time_data = self._zeros
        playing = False
        rate = self.FILE_RATE

        if self.audio_mode == "FILE":
            if self.track_active and not self.paused:
                playing = pygame.mixer.music.get_busy()
                if playing and self.np_audio_data.size > 0:
                    current_ms = self.get_position_ms() + self.AUDIO_DELAY_OFFSET
                    sample_idx = int((current_ms / 1000.0) * self.FILE_RATE)
                    data = self.np_audio_data
                    if sample_idx + self.FFT_SIZE < data.size:
                        time_data = data[sample_idx : sample_idx + self.FFT_SIZE]
                    elif sample_idx < data.size:
                        time_data = np.pad(
                            data[sample_idx:],
                            (0, self.FFT_SIZE - (data.size - sample_idx)),
                            "constant",
                        )
        elif self.audio_mode == "SYSTEM":
            playing = True
            time_data = self.sys_audio_buffer
            rate = self.sys_rate

        if playing:
            # 示波器平滑
            w = 0.4 ** k
            self.osc_smoothed = self.osc_smoothed * w + time_data * (1.0 - w)

            # FFT -> 每根柱子取樣（線性內插）-> dB -> 對比
            fft_abs = np.abs(np.fft.rfft(time_data * self.window)) / self.FFT_SIZE
            amp = self._band_amplitudes(fft_abs, rate)
            db = 20.0 * np.log10(amp + 1e-6)
            norm_val = np.clip((db + 85.0) / 85.0, 0.0, 1.0)
            raw = (norm_val ** 2.8) * 255.0

            # 低音強拍震動
            bass_energy = float(np.mean(raw[:12])) / 255.0
            if bass_energy > 0.5:
                self.shake_amount = max(
                    self.shake_amount, (bass_energy - 0.45) * 45.0
                )

            # 頻率越高平滑越弱
            stc = self.stc ** k
            self.fft_smoothed = self.fft_smoothed * stc + raw * (1.0 - stc)
        else:
            self.fft_smoothed *= 0.6 ** k
            self.osc_smoothed *= 0.6 ** k

        # 柱高：快升快降（升 0.98 / 降 38~68 px 每幀 @120FPS），Peak Cap 每幀降 12
        norm = self.fft_smoothed / 255.0
        val = (norm ** self.power_exp) * self.height_mult
        rise = 0.02 ** k
        self.smoothed_values = np.where(
            val > self.smoothed_values,
            self.smoothed_values * rise + val * (1.0 - rise),
            np.maximum(0.0, self.smoothed_values - self.fall_speed * k),
        )
        self.peaks = np.where(
            self.smoothed_values > self.peaks,
            self.smoothed_values,
            np.maximum(0.0, self.peaks - 12.0 * k),
        )

    # ------------------------------------------------------------------ 繪圖
    def draw_gear_icon(self, surface, cx, cy, r, color):
        pygame.draw.circle(surface, color, (cx, cy), r, width=2)
        pygame.draw.circle(surface, color, (cx, cy), int(r * 0.4))
        for i in range(8):
            angle = math.radians(i * 45)
            x1 = cx + int((r - 2) * math.cos(angle))
            y1 = cy + int((r - 2) * math.sin(angle))
            x2 = cx + int((r + 4) * math.cos(angle))
            y2 = cy + int((r + 4) * math.sin(angle))
            pygame.draw.line(surface, color, (x1, y1), (x2, y2), 3)

    def draw_button_box(self, rect, hover=False, radius=6, fill=None):
        pygame.draw.rect(
            self.screen,
            fill or ((55, 25, 95) if hover else (35, 15, 65)),
            rect,
            border_radius=radius,
        )
        pygame.draw.rect(
            self.screen, self.theme_color, rect, width=2, border_radius=radius
        )

    def draw_centered(self, font, text, color, rect):
        surf = self.render_text(font, text, color)
        self.screen.blit(
            surf,
            (
                rect.centerx - surf.get_width() // 2,
                rect.centery - surf.get_height() // 2,
            ),
        )

    def _gradient_rows(self, h, theme):
        """高度 h 的柱子，每一列預先和背景混好的顏色（上方不透明、往下漸漸透明）。"""
        alpha = self._alpha_cache.get(h)
        if alpha is None:
            if len(self._alpha_cache) > 1500:
                self._alpha_cache.clear()
            ratio = 1.0 - np.arange(h) / h
            alpha = np.floor(255.0 * ratio ** 1.5) / 255.0
            self._alpha_cache[h] = alpha
        bg = np.array(self.BG_DARK, dtype=np.float32)
        fg = np.array(theme, dtype=np.float32)
        return (bg + (fg - bg) * alpha[:, None]).astype(np.uint8)

    def _gradient_strip(self, h):
        theme = self.theme_color
        if theme != self._grad_color:  # 換主題色時才清快取
            self._grad_cache.clear()
            self._grad_color = theme
        strip = self._grad_cache.get(h)
        if strip is None:
            if len(self._grad_cache) > 1500:
                self._grad_cache.clear()
            rows = self._gradient_rows(h, theme)
            arr = np.empty((self.B_WIDTH, h, 3), dtype=np.uint8)
            arr[:] = rows[None, :, :]
            strip = pygame.surfarray.make_surface(arr).convert()
            self._grad_cache[h] = strip
        return strip

    def draw_spectrum(self, shake_x, shake_y):
        b_width, b_gap = self.B_WIDTH, self.B_GAP
        total_w = (b_width + b_gap) * self.BAR_COUNT
        start_x = (
            self.sidebar_w
            + ((self.WIDTH - self.sidebar_w) - total_w) // 2
            + int(shake_x)
        )
        panel_y = self.HEIGHT + int(shake_y)  # 柱子從視窗最底部長出來

        theme = self.theme_color
        a = 220 / 255.0  # Peak Cap 原本 alpha 220，預先和背景混色
        peak_col = tuple(
            int(theme[c] * a + self.BG_DARK[c] * (1 - a)) for c in range(3)
        )

        heights = self.smoothed_values.astype(np.int32).tolist()
        peaks = self.peaks.tolist()
        screen = self.screen
        for i in range(self.BAR_COUNT):
            x = start_x + i * (b_width + b_gap)
            h = heights[i]
            if h > 0:
                screen.blit(self._gradient_strip(h), (x, panel_y - h))
            if peaks[i] > 0:
                p_y = panel_y - int(peaks[i]) - 12
                if p_y > 0:
                    screen.fill(peak_col, (x, p_y, b_width, 3))

    def draw_oscilloscope(self, shake_x, shake_y):
        center_y = 600 + int(shake_y)
        amp = 180
        v_width = self.WIDTH - self.sidebar_w
        xs = (
            self.sidebar_w
            + (self.osc_xfrac * v_width).astype(np.int32)
            + int(shake_x)
        )
        ys = center_y + np.clip(
            self.osc_smoothed[self.osc_idx] * amp * 1.5, -amp, amp
        ).astype(np.int32)
        pts = np.stack((xs, ys), axis=1).tolist()
        if len(pts) > 1:
            pygame.draw.lines(self.screen, self.WHITE, False, pts, 2)

    def draw_title(self):
        text = "LOADING..." if self.is_loading else self.song_name
        max_w = self.WIDTH - self.sidebar_w - 160
        text = self.fit_text(self.font_title, text, max_w)
        surf = self.font_title.render(text, True, self.theme_color)
        self.screen.blit(surf, (self.sidebar_w + 80, 100))

    def draw_progress(self):
        rect = self.progress_rect()
        pygame.draw.rect(self.screen, (40, 30, 60), rect)

        duration = self.audio_duration_ms
        if self.dragging_seek:
            display_ms = self.seek_pct * duration
        else:
            display_ms = self.get_position_ms()

        pct = 0.0
        if self.track_active and duration > 0:
            pct = max(0.0, min(1.0, display_ms / duration))
        if pct > 0:
            pygame.draw.rect(
                self.screen,
                self.theme_color,
                (rect.x, rect.y, int(rect.w * pct), rect.h),
            )

        color = self.theme_color
        self.screen.blit(
            self.render_text(self.font_time, self.format_time(display_ms), color),
            (rect.x, 55),
        )
        txt_t = self.render_text(
            self.font_time, self.format_time(max(0, duration)), color
        )
        self.screen.blit(txt_t, (rect.right - txt_t.get_width(), 55))

    def draw_transport(self, mx, my):
        tr = self.transport_rects()
        col = self.theme_color
        for name in ("prev", "play", "next"):
            self.draw_button_box(
                tr[name], tr[name].collidepoint(mx, my), radius=8
            )
        # 上一首
        cx, cy = tr["prev"].center
        pygame.draw.rect(self.screen, col, (cx - 10, cy - 9, 3, 18))
        pygame.draw.polygon(
            self.screen, col, [(cx + 9, cy - 10), (cx + 9, cy + 10), (cx - 6, cy)]
        )
        # 播放 / 暫停
        cx, cy = tr["play"].center
        if self.track_active and not self.paused:
            pygame.draw.rect(self.screen, col, (cx - 9, cy - 10, 6, 20))
            pygame.draw.rect(self.screen, col, (cx + 3, cy - 10, 6, 20))
        else:
            pygame.draw.polygon(
                self.screen,
                col,
                [(cx - 7, cy - 11), (cx - 7, cy + 11), (cx + 10, cy)],
            )
        # 下一首
        cx, cy = tr["next"].center
        pygame.draw.rect(self.screen, col, (cx + 7, cy - 9, 3, 18))
        pygame.draw.polygon(
            self.screen, col, [(cx - 9, cy - 10), (cx - 9, cy + 10), (cx + 6, cy)]
        )
        # 音量
        vol = tr["vol"]
        pygame.draw.rect(self.screen, (40, 30, 60), vol, border_radius=4)
        pygame.draw.rect(
            self.screen,
            col,
            (vol.x, vol.y, int(vol.w * self.volume), vol.h),
            border_radius=4,
        )
        pygame.draw.circle(
            self.screen,
            self.WHITE,
            (vol.x + int(vol.w * self.volume), vol.centery),
            8,
        )
        label = self.render_text(
            self.font_time, f"VOL {int(round(self.volume * 100))}%", col
        )
        self.screen.blit(label, (vol.right + 20, vol.centery - label.get_height() // 2))

    def draw_sidebar(self, mx, my):
        w = self.sidebar_w
        if w <= 0:
            return
        clip = pygame.Rect(0, 0, w, self.HEIGHT)
        self.screen.set_clip(clip)  # 直接在 screen 上裁切繪製，不再每幀建立 Surface
        self.screen.fill(self.SIDEBAR_BG, clip)

        btn_rect = pygame.Rect(10, 10, w - 20, 40)
        if btn_rect.width > 40:
            self.draw_button_box(btn_rect, radius=5)
            self.draw_centered(
                self.font_ui, "SELECT FOLDER", self.theme_color, btn_rect
            )

        if w > 40:
            first = max(0, (40 - 70 - self.scroll_y) // 35)
            last = min(
                len(self.playlist), (self.HEIGHT - 70 - self.scroll_y) // 35 + 1
            )
            for i in range(first, last):
                rect = self.item_rect(i)
                rect.width = w - 20
                is_current = i == self.current_idx and self.audio_mode == "FILE"
                if rect.collidepoint(mx, my):
                    pygame.draw.rect(
                        self.screen, (55, 25, 95), rect, border_radius=4
                    )
                elif is_current:
                    pygame.draw.rect(
                        self.screen, (25, 45, 75), rect, border_radius=4
                    )
                name = os.path.splitext(os.path.basename(self.playlist[i]))[0]
                name = self.fit_text(
                    self.font_ui, f"{i + 1}. {name}", self.MAX_SIDEBAR_W - 60
                )
                txt = self.render_text(
                    self.font_ui,
                    name,
                    self.theme_color if is_current else self.WHITE,
                )
                self.screen.blit(txt, (20, rect.y + 3))

        self.screen.set_clip(None)
        pygame.draw.line(
            self.screen, self.theme_color, (w, 0), (w, self.HEIGHT), 2
        )

    def draw_toggle_button(self):
        self.draw_button_box(self.toggle_btn_rect, radius=4)
        arrow = "<" if self.sidebar_target_w == self.MAX_SIDEBAR_W else ">"
        self.draw_centered(
            self.font_btn, arrow, self.theme_color, self.toggle_btn_rect
        )

    def draw_settings(self):
        self.draw_button_box(self.setting_btn_rect)
        self.draw_gear_icon(
            self.screen,
            self.setting_btn_rect.centerx,
            self.setting_btn_rect.centery,
            10,
            self.theme_color,
        )
        if not self.show_settings:
            return

        p = self.panel_rect
        pygame.draw.rect(self.screen, (25, 10, 45), p, border_radius=8)
        pygame.draw.rect(
            self.screen, self.theme_color, p, width=2, border_radius=8
        )
        self.screen.blit(
            self.render_text(self.font_ui, "THEME COLOR", self.WHITE),
            (p.x + 15, p.y + 12),
        )
        for key, rect in self.theme_rects.items():
            pygame.draw.rect(self.screen, self.THEMES[key], rect, border_radius=4)
            if key == self.current_theme_key:
                pygame.draw.rect(
                    self.screen, self.WHITE, rect, width=3, border_radius=4
                )

        sys_on = self.audio_mode == "SYSTEM"
        self.draw_button_box(
            self.sys_btn_rect,
            radius=5,
            fill=(60, 20, 80) if sys_on else (35, 15, 65),
        )
        self.draw_centered(
            self.font_ui,
            "AUDIO: SYSTEM" if sys_on else "AUDIO: FILE PLAY",
            self.theme_color,
            self.sys_btn_rect,
        )

        self.draw_button_box(
            self.fs_btn_rect,
            radius=5,
            fill=(60, 20, 80) if self.fullscreen else (35, 15, 65),
        )
        self.draw_centered(
            self.font_ui,
            "FULLSCREEN: ON (F11)" if self.fullscreen else "FULLSCREEN: OFF (F11)",
            self.theme_color,
            self.fs_btn_rect,
        )

    # ------------------------------------------------------------------ 主迴圈
    def run(self):
        dt = 1.0 / self.FPS
        while self.running:
            k = dt * REF_FPS
            mx, my = pygame.mouse.get_pos()

            self.handle_events()
            self.update_animation(k)
            self.check_track_end()

            shake_x = shake_y = 0.0
            if self.shake_amount > 0.5:
                shake_x = random.uniform(-self.shake_amount, self.shake_amount)
                shake_y = random.uniform(-self.shake_amount, self.shake_amount)
                self.shake_amount *= 0.88 ** k

            self.update_audio(k)

            self.screen.fill(self.BG_DARK)
            self.draw_spectrum(shake_x, shake_y)
            self.draw_oscilloscope(shake_x, shake_y)
            self.draw_title()
            if self.audio_mode == "FILE":
                self.draw_progress()
                self.draw_transport(mx, my)
            self.draw_sidebar(mx, my)
            if self.audio_mode == "FILE":
                self.draw_toggle_button()
            self.draw_settings()

            pygame.display.flip()
            dt = min(self.clock.tick(self.FPS) / 1000.0, 0.1)

        self.is_sys_listening = False
        self.sys_gen += 1
        pygame.mixer.music.stop()
        pygame.quit()
        sys.exit()


if __name__ == "__main__":
    app = MusiCanApp()
    app.run()
