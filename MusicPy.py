import pygame
import numpy as np
import sys
import os
import math
import threading
import random
from pydub import AudioSegment
import tkinter as tk
from tkinter import filedialog

try:
    import pyaudiowpatch as pyaudio
    HAS_PYAUDIO = True
except ImportError:
    try:
        import pyaudio
        HAS_PYAUDIO = True
    except ImportError:
        HAS_PYAUDIO = False

class MusiCanApp:
    def __init__(self):
        pygame.init()
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
        
        self.WIDTH, self.HEIGHT = 1920, 1080
        self.MAX_SIDEBAR_W = 350
        self.sidebar_w = self.MAX_SIDEBAR_W 
        self.sidebar_target_w = self.MAX_SIDEBAR_W
        
        self.FPS = 120
        self.BAR_COUNT = 130
        self.FFT_SIZE = 2048
        self.AUDIO_DELAY_OFFSET = 120 
        
        flags = pygame.DOUBLEBUF | pygame.HWSURFACE
        try:
            self.screen = pygame.display.set_mode((self.WIDTH, self.HEIGHT), flags, vsync=1)
        except TypeError:
            self.screen = pygame.display.set_mode((self.WIDTH, self.HEIGHT), flags)
            
        pygame.display.set_caption("MusiCan Studio - Perfect Web-Audio Engine")
        self.clock = pygame.time.Clock()
        self.canvas = pygame.Surface((self.WIDTH, self.HEIGHT), pygame.SRCALPHA)
        
        # Fonts
        self.font_title = pygame.font.SysFont("Microsoft JhengHei", 65, bold=True)
        self.font_ui = pygame.font.SysFont("Microsoft JhengHei", 18)
        self.font_time = pygame.font.SysFont("monospace", 18)
        self.font_btn = pygame.font.SysFont("monospace", 22, bold=True)
        
        # 主題顏色
        self.THEMES = {
            "RED": (255, 60, 80),
            "GREEN": (0, 255, 128),
            "BLUE": (0, 180, 255),
            "WHITE": (240, 240, 240)
        }
        self.current_theme_key = "BLUE"
        self.target_color = np.array(self.THEMES[self.current_theme_key], dtype=np.float32)
        self.current_color = np.array(self.THEMES[self.current_theme_key], dtype=np.float32)
        
        self.BG_DARK = (10, 0, 24)
        self.SIDEBAR_BG = (18, 5, 38)
        self.WHITE = (255, 255, 255)
        
        # UI 設定
        self.show_settings = False
        self.setting_btn_rect = pygame.Rect(self.WIDTH - 60, 20, 40, 40)
        self.audio_mode = "FILE"
        
        self.pa = None
        self.sys_stream = None
        self.sys_audio_buffer = np.zeros(self.FFT_SIZE, dtype=np.float32)
        self.is_sys_listening = False

        # Audio Buffers
        self.playlist = []
        self.current_idx = -1
        self.song_name = "WAITING..."
        self.np_audio_data = np.array([], dtype=np.float32)
        self.audio_duration_ms = 0
        
        # FFT Buffer
        self.fft_smoothed = np.zeros(self.BAR_COUNT)
        self.smoothed_values = np.zeros(self.BAR_COUNT)
        self.peaks = np.zeros(self.BAR_COUNT)
        self.osc_smoothed = np.zeros(self.FFT_SIZE, dtype=np.float32)
        
        self.shake_amount = 0.0
        self.scroll_y = 0
        self.toggle_btn_rect = pygame.Rect(self.sidebar_w, 20, 35, 45)
        self.is_loading = False

    @property
    def theme_color(self):
        return (int(self.current_color[0]), int(self.current_color[1]), int(self.current_color[2]))

    def toggle_system_audio(self):
        if not HAS_PYAUDIO:
            print("pip install pyaudiowpatch")
            return

        if self.audio_mode == "SYSTEM":
            self.audio_mode = "FILE"
            self.is_sys_listening = False
            self.sys_audio_buffer.fill(0)
            self.song_name = "WAITING..."
        else:
            pygame.mixer.music.stop()
            self.audio_mode = "SYSTEM"
            self.song_name = "SYSTEM AUDIO LOOPBACK"
            self.sidebar_target_w = 0 
            self.is_sys_listening = True
            
            t = threading.Thread(target=self._start_pyaudio_loopback, daemon=True)
            t.start()

    def _start_pyaudio_loopback(self):
        pa_instance = None
        stream_instance = None
        try:
            pa_instance = pyaudio.PyAudio()
            self.pa = pa_instance
            
            try:
                loopback_device = pa_instance.get_default_wasapi_loopback()
            except AttributeError:
                wasapi_info = pa_instance.get_host_api_info_by_type(pyaudio.paWASAPI)
                default_speakers = pa_instance.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
                
                loopback_device = None
                for i in range(pa_instance.get_device_count()):
                    dev = pa_instance.get_device_info_by_index(i)
                    if dev.get("isLoopbackDevice") and default_speakers["name"] in dev["name"]:
                        loopback_device = dev
                        break
                        
                if not loopback_device:
                    raise RuntimeError("找不到預設的 Loopback 音訊裝置")

            rate = int(loopback_device["defaultSampleRate"])
            channels = loopback_device["maxInputChannels"]

            stream_instance = pa_instance.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=loopback_device["index"],
                frames_per_buffer=self.FFT_SIZE
            )
            self.sys_stream = stream_instance

            while self.is_sys_listening:
                try:
                    data = stream_instance.read(self.FFT_SIZE, exception_on_overflow=False)
                except Exception:
                    break
                
                if not self.is_sys_listening:
                    break

                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                
                # 自動動態增益
                max_amp = np.max(np.abs(samples))
                if max_amp > 0.001:
                    gain = min(15.0, 0.85 / max_amp)
                    samples = samples * gain

                # 雙聲道轉單聲道
                if channels >= 2:
                    mono = samples[0::channels]
                else:
                    mono = samples
                
                if len(mono) < self.FFT_SIZE:
                    mono = np.pad(mono, (0, self.FFT_SIZE - len(mono)), 'constant')
                else:
                    mono = mono[:self.FFT_SIZE]
                    
                self.sys_audio_buffer = mono

        except Exception as e:
            print(f"System Audio Error: {e}")
            if self.audio_mode == "SYSTEM":
                self.audio_mode = "FILE"
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

    def load_folder(self):
        root = tk.Tk()
        root.withdraw()
        folder = filedialog.askdirectory(title="Select Music Folder")
        if folder:
            self.playlist = []
            for f in os.listdir(folder):
                if f.lower().endswith(('.mp3', '.wav')):
                    self.playlist.append(os.path.join(folder, f))
            self.current_idx = -1
            self.scroll_y = 0

    def _async_load_and_play(self, path):
        try:
            if path.lower().endswith('.wav'):
                audio = AudioSegment.from_wav(path)
            else:
                audio = AudioSegment.from_mp3(path)
                
            self.audio_duration_ms = len(audio)
            audio = audio.set_channels(1).set_frame_rate(44100)
            raw = audio.get_array_of_samples()

            self.np_audio_data = np.array(raw, dtype=np.float32) / (2.0 ** 15)
            
            self.fft_smoothed.fill(0)
            self.smoothed_values.fill(0)
            self.peaks.fill(0)
            self.osc_smoothed.fill(0)
            
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
        except Exception as e:
            print(f"Error loading audio: {e}")
        finally:
            self.is_loading = False

    def play_song(self, idx):
        if idx < 0 or idx >= len(self.playlist) or self.is_loading: return

        if self.audio_mode == "SYSTEM":
            self.is_sys_listening = False
            
        self.audio_mode = "FILE"
        self.is_loading = True
        self.current_idx = idx
        path = self.playlist[idx]
        self.song_name = os.path.splitext(os.path.basename(path))[0].upper()
        self.sidebar_target_w = 0 
        
        t = threading.Thread(target=self._async_load_and_play, args=(path,), daemon=True)
        t.start()

    def format_time(self, ms):
        if ms < 0: ms = 0
        s = ms // 1000
        m = s // 60
        sec = s % 60
        return f"{m}:{sec:02d}"

    def draw_gradient_bar(self, surface, x, y, width, height):
        if height <= 0: return
        bar_surf = pygame.Surface((width, height), pygame.SRCALPHA)
        r, g, b = self.theme_color
        
        for row in range(height):
            alpha_ratio = 1.0 - (row / height)
            alpha = int(255 * (alpha_ratio ** 1.5))
            pygame.draw.line(bar_surf, (r, g, b, alpha), (0, row), (width, row))
            
        surface.blit(bar_surf, (x, y))

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

    def run(self):
        running = True
        while running:
            mx, my = pygame.mouse.get_pos()
            
            self.sidebar_w = int(self.sidebar_w * 0.8 + self.sidebar_target_w * 0.2)
            if abs(self.sidebar_w - self.sidebar_target_w) < 2:
                self.sidebar_w = self.sidebar_target_w
            self.toggle_btn_rect.x = self.sidebar_w + 5

            self.current_color += (self.target_color - self.current_color) * 0.1

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1: 
                        if self.setting_btn_rect.collidepoint(mx, my):
                            self.show_settings = not self.show_settings
                            continue

                        if self.show_settings:
                            panel_rect = pygame.Rect(self.WIDTH - 280, 70, 260, 140)
                            if panel_rect.collidepoint(mx, my):
                                colors_keys = list(self.THEMES.keys())
                                for idx, key in enumerate(colors_keys):
                                    c_rect = pygame.Rect(self.WIDTH - 260 + idx * 55, 110, 45, 30)
                                    if c_rect.collidepoint(mx, my):
                                        self.current_theme_key = key
                                        self.target_color = np.array(self.THEMES[key], dtype=np.float32)
                                
                                sys_btn = pygame.Rect(self.WIDTH - 260, 158, 220, 35)
                                if sys_btn.collidepoint(mx, my):
                                    self.toggle_system_audio()
                                continue
                            else:
                                self.show_settings = False

                        if self.audio_mode == "FILE" and self.toggle_btn_rect.collidepoint(mx, my):
                            self.sidebar_target_w = 0 if self.sidebar_target_w == self.MAX_SIDEBAR_W else self.MAX_SIDEBAR_W
                            continue
                        
                        if self.sidebar_target_w == self.MAX_SIDEBAR_W and mx < self.MAX_SIDEBAR_W:
                            btn_rect = pygame.Rect(10, 10, self.MAX_SIDEBAR_W - 20, 40)
                            if btn_rect.collidepoint(mx, my):
                                self.load_folder()
                            elif my > 60:
                                item_y = 70 + self.scroll_y
                                for i in range(len(self.playlist)):
                                    rect = pygame.Rect(10, item_y, self.MAX_SIDEBAR_W - 20, 30)
                                    if rect.collidepoint(mx, my):
                                        self.play_song(i)
                                        break
                                    item_y += 35
                                    
                    elif event.button == 4 and self.sidebar_target_w == self.MAX_SIDEBAR_W and mx < self.MAX_SIDEBAR_W: 
                        self.scroll_y = min(0, self.scroll_y + 35)
                    elif event.button == 5 and self.sidebar_target_w == self.MAX_SIDEBAR_W and mx < self.MAX_SIDEBAR_W:
                        max_scroll = -max(0, len(self.playlist) * 35 - (self.HEIGHT - 80))
                        self.scroll_y = max(max_scroll, self.scroll_y - 35)

            self.screen.fill(self.BG_DARK)
            self.canvas.fill((0, 0, 0, 0))
            
            shake_x, shake_y = 0, 0
            if self.shake_amount > 0.5:
                shake_x = random.uniform(-self.shake_amount, self.shake_amount)
                shake_y = random.uniform(-self.shake_amount, self.shake_amount)
                self.shake_amount *= 0.88
            
            raw_freq_bars = np.zeros(self.BAR_COUNT, dtype=np.float32)
            time_data = np.zeros(self.FFT_SIZE, dtype=np.float32)
            playing = False

            if self.audio_mode == "FILE":
                raw_current_ms = pygame.mixer.music.get_pos()
                playing = pygame.mixer.music.get_busy() and raw_current_ms >= 0
                if playing and self.np_audio_data.size > 0:
                    current_ms = raw_current_ms + self.AUDIO_DELAY_OFFSET
                    sample_idx = int((current_ms / 1000.0) * 44100)
                    if sample_idx + self.FFT_SIZE < self.np_audio_data.size:
                        time_data = self.np_audio_data[sample_idx : sample_idx + self.FFT_SIZE]
                    elif sample_idx < self.np_audio_data.size:
                        time_data = self.np_audio_data[sample_idx:]
                        time_data = np.pad(time_data, (0, self.FFT_SIZE - len(time_data)), 'constant')
            elif self.audio_mode == "SYSTEM":
                playing = True
                raw_current_ms = 0
                time_data = self.sys_audio_buffer

            if playing:
                self.osc_smoothed = self.osc_smoothed * 0.4 + time_data * 0.6
                
                windowed_time = time_data * np.hanning(self.FFT_SIZE) * 2.0
                fft_res = np.fft.rfft(windowed_time)
                fft_abs = np.abs(fft_res) / self.FFT_SIZE
                
                min_hz = 42.0
                max_hz = 15500.0
                
                for i in range(self.BAR_COUNT):
                    pct = i / (self.BAR_COUNT - 1)
                    target_hz = min_hz * math.pow(max_hz / min_hz, pct)
                    
                    bin_idx = target_hz * self.FFT_SIZE / 44100.0
                    idx0 = max(0, min(self.FFT_SIZE // 2, math.floor(bin_idx)))
                    idx1 = max(0, min(self.FFT_SIZE // 2, math.ceil(bin_idx)))
                    t = bin_idx - idx0
                    
                    amplitude = fft_abs[idx0] * (1.0 - t) + fft_abs[idx1] * t
                    db = 20.0 * math.log10(amplitude + 1e-6)
                    normalized_val = max(0.0, min(1.0, (db + 85.0) / 85.0))
                    contrast_val = math.pow(normalized_val, 2.8)
                    raw_freq_bars[i] = contrast_val * 255.0

                bass_energy = np.mean(raw_freq_bars[:12]) / 255.0
                if bass_energy > 0.5:
                    self.shake_amount = max(self.shake_amount, (bass_energy - 0.45) * 45.0)

                for i in range(self.BAR_COUNT):
                    pct = i / (self.BAR_COUNT - 1)
                    stc = 0.55 - (pct * 0.15)
                    self.fft_smoothed[i] = self.fft_smoothed[i] * stc + raw_freq_bars[i] * (1.0 - stc)
            else:
                self.fft_smoothed *= 0.6
                self.osc_smoothed *= 0.6

            b_width = 6
            b_gap = 9
            total_w = (b_width + b_gap) * self.BAR_COUNT
            start_x = self.sidebar_w + ((self.WIDTH - self.sidebar_w) - total_w) // 2 + int(shake_x)
            panel_y = self.HEIGHT + int(shake_y)
            
            for i in range(self.BAR_COUNT):
                progress = i / (self.BAR_COUNT - 1)
                power_exponent = 1.4 - (progress * 0.2)
                height_multiplier = 780.0 - (progress * 180.0) 
                    
                norm_val = self.fft_smoothed[i] / 255.0
                val = (norm_val ** power_exponent) * height_multiplier
                
                if val > self.smoothed_values[i]:
                    self.smoothed_values[i] = self.smoothed_values[i] * 0.02 + val * 0.98
                else:
                    fall_speed = 38.0 + (progress * 30.0)
                    self.smoothed_values[i] = max(0.0, self.smoothed_values[i] - fall_speed)
                    
                if self.smoothed_values[i] > self.peaks[i]:
                    self.peaks[i] = self.smoothed_values[i]
                else:
                    self.peaks[i] = max(0.0, self.peaks[i] - 12.0)
                    
                current_x = start_x + i * (b_width + b_gap)
                bar_h = int(self.smoothed_values[i])
                
                if bar_h > 0:
                    self.draw_gradient_bar(self.canvas, current_x, panel_y - bar_h, b_width, bar_h)
                
                if self.peaks[i] > 0:
                    p_y = panel_y - int(self.peaks[i]) - 12
                    if p_y > 0:
                        r, g, b = self.theme_color
                        pygame.draw.rect(self.canvas, (r, g, b, 220), (current_x, p_y, b_width, 3))

            self.screen.blit(self.canvas, (0, 0))

            center_y_osc = 600 + int(shake_y)
            amp = 180
            v_width = self.WIDTH - self.sidebar_w
            
            osc_points = []
            step = max(1, self.FFT_SIZE // 180) 
            for idx in range(0, self.FFT_SIZE, step):
                x = self.sidebar_w + int((idx / self.FFT_SIZE) * v_width) + int(shake_x)
                y_offset = max(-amp, min(amp, self.osc_smoothed[idx] * amp * 1.5))
                y = int(center_y_osc + y_offset)
                osc_points.append((x, y))
                
            if len(osc_points) > 1:
                pygame.draw.lines(self.screen, self.WHITE, False, osc_points, 2)

            display_title = "LOADING..." if self.is_loading else self.song_name
            txt_title = self.font_title.render(display_title, True, self.theme_color)
            self.screen.blit(txt_title, (self.sidebar_w + 80, 100))
            
            if self.audio_mode == "FILE":
                p_container_x = self.sidebar_w + int((self.WIDTH - self.sidebar_w) * 0.05)
                p_container_w = int((self.WIDTH - self.sidebar_w) * 0.90)
                pygame.draw.rect(self.screen, (40, 30, 60), (p_container_x, 40, p_container_w, 8))
                
                progress_pct = 0.0
                display_ms = raw_current_ms if playing else 0
                if playing and self.audio_duration_ms > 0:
                    progress_pct = min(1.0, display_ms / self.audio_duration_ms)
                if progress_pct > 0:
                    pygame.draw.rect(self.screen, self.theme_color, (p_container_x, 40, int(p_container_w * progress_pct), 8))
                    
                str_curr = self.format_time(display_ms)
                str_total = self.format_time(self.audio_duration_ms if self.audio_duration_ms > 0 else 0)
                self.screen.blit(self.font_time.render(str_curr, True, self.theme_color), (p_container_x, 55))
                txt_t = self.font_time.render(str_total, True, self.theme_color)
                self.screen.blit(txt_t, (p_container_x + p_container_w - txt_t.get_width(), 55))

            if self.sidebar_w > 0:
                sidebar_surf = pygame.Surface((self.sidebar_w, self.HEIGHT))
                sidebar_surf.fill(self.SIDEBAR_BG)
                
                btn_rect = pygame.Rect(10, 10, self.sidebar_w - 20, 40)
                if btn_rect.width > 40:
                    pygame.draw.rect(sidebar_surf, (35, 15, 65), btn_rect, border_radius=5)
                    pygame.draw.rect(sidebar_surf, self.theme_color, btn_rect, width=2, border_radius=5)
                    txt_btn = self.font_ui.render("SELECT FOLDER", True, self.theme_color)
                    sidebar_surf.blit(txt_btn, (btn_rect.centerx - txt_btn.get_width() // 2, btn_rect.centery - txt_btn.get_height() // 2))
                
                item_y = 70 + self.scroll_y
                for i, p in enumerate(self.playlist):
                    if 40 <= item_y <= self.HEIGHT and self.sidebar_w > 40:
                        rect = pygame.Rect(10, item_y, self.sidebar_w - 20, 30)
                        is_current = (i == self.current_idx and self.audio_mode == "FILE")
                        if rect.collidepoint(mx, my):
                            pygame.draw.rect(sidebar_surf, (55, 25, 95), rect, border_radius=4)
                        elif is_current:
                            pygame.draw.rect(sidebar_surf, (25, 45, 75), rect, border_radius=4)
                            
                        name_cropped = os.path.basename(p)[:24]
                        txt_item = self.font_ui.render(f"{i+1}. {name_cropped}", True, self.theme_color if is_current else self.WHITE)
                        sidebar_surf.blit(txt_item, (20, item_y + 3))
                    item_y += 35
                
                self.screen.blit(sidebar_surf, (0, 0))
                pygame.draw.line(self.screen, self.theme_color, (self.sidebar_w, 0), (self.sidebar_w, self.HEIGHT), 2)

            if self.audio_mode == "FILE":
                pygame.draw.rect(self.screen, (35, 15, 65), self.toggle_btn_rect, border_radius=4)
                pygame.draw.rect(self.screen, self.theme_color, self.toggle_btn_rect, width=2, border_radius=4)
                arrow_str = "<" if self.sidebar_target_w == self.MAX_SIDEBAR_W else ">"
                txt_arrow = self.font_btn.render(arrow_str, True, self.theme_color)
                self.screen.blit(txt_arrow, (self.toggle_btn_rect.centerx - txt_arrow.get_width() // 2, self.toggle_btn_rect.centery - txt_arrow.get_height() // 2))

            pygame.draw.rect(self.screen, (35, 15, 65), self.setting_btn_rect, border_radius=6)
            pygame.draw.rect(self.screen, self.theme_color, self.setting_btn_rect, width=2, border_radius=6)
            self.draw_gear_icon(self.screen, self.setting_btn_rect.centerx, self.setting_btn_rect.centery, 10, self.theme_color)

            if self.show_settings:
                panel_rect = pygame.Rect(self.WIDTH - 280, 70, 260, 140)
                pygame.draw.rect(self.screen, (25, 10, 45), panel_rect, border_radius=8)
                pygame.draw.rect(self.screen, self.theme_color, panel_rect, width=2, border_radius=8)
                
                lbl_color = self.font_ui.render("THEME COLOR", True, self.WHITE)
                self.screen.blit(lbl_color, (panel_rect.x + 15, panel_rect.y + 12))
                
                colors_keys = list(self.THEMES.keys())
                for idx, key in enumerate(colors_keys):
                    c_rect = pygame.Rect(panel_rect.x + 20 + idx * 55, panel_rect.y + 40, 45, 30)
                    color_val = self.THEMES[key]
                    pygame.draw.rect(self.screen, color_val, c_rect, border_radius=4)
                    if key == self.current_theme_key:
                        pygame.draw.rect(self.screen, self.WHITE, c_rect, width=3, border_radius=4)
                
                sys_btn = pygame.Rect(panel_rect.x + 20, panel_rect.y + 88, 220, 35)
                sys_bg = (60, 20, 80) if self.audio_mode == "SYSTEM" else (35, 15, 65)
                pygame.draw.rect(self.screen, sys_bg, sys_btn, border_radius=5)
                pygame.draw.rect(self.screen, self.theme_color, sys_btn, width=2, border_radius=5)
                
                mode_str = "AUDIO: SYSTEM" if self.audio_mode == "SYSTEM" else "AUDIO: FILE PLAY"
                txt_mode = self.font_ui.render(mode_str, True, self.theme_color)
                self.screen.blit(txt_mode, (sys_btn.centerx - txt_mode.get_width() // 2, sys_btn.centery - txt_mode.get_height() // 2))

            pygame.display.flip()
            self.clock.tick(self.FPS)

        self.is_sys_listening = False
        pygame.mixer.music.stop()
        pygame.quit()
        sys.exit()

if __name__ == "__main__":
    app = MusiCanApp()
    app.run()
