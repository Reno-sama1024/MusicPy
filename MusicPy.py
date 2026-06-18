import pygame
import numpy as np
import sys
import os
import math
import threading
from pydub import AudioSegment
import tkinter as tk
from tkinter import filedialog

class MusiCanApp:
    def __init__(self):
        pygame.init()
        # 放大音訊緩衝區，確保 FFT 採樣時域數據足夠飽滿
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
        
        self.WIDTH, self.HEIGHT = 1920, 1080
        self.MAX_SIDEBAR_W = 350
        self.sidebar_w = self.MAX_SIDEBAR_W 
        self.sidebar_target_w = self.MAX_SIDEBAR_W
        
        self.FPS = 120
        self.BAR_COUNT = 130
        self.FFT_SIZE = 2048
        
        self.screen = pygame.display.set_mode((self.WIDTH, self.HEIGHT))
        pygame.display.set_caption("MusiCan Studio - Perfect Web-Audio Engine")
        self.clock = pygame.time.Clock()
        
        # Fonts
        self.font_title = pygame.font.SysFont("Microsoft JhengHei", 65, bold=True)
        self.font_ui = pygame.font.SysFont("Microsoft JhengHei", 18)
        self.font_time = pygame.font.SysFont("monospace", 18)
        self.font_btn = pygame.font.SysFont("monospace", 22, bold=True)
        
        # Colors 
        self.NEON_CYAN = (0, 255, 255)
        self.BG_DARK = (10, 0, 24)
        self.SIDEBAR_BG = (18, 5, 38)
        self.WHITE = (255, 255, 255)
        
        # Audio Buffers
        self.playlist = []
        self.current_idx = -1
        self.song_name = "WAITING..."
        self.np_audio_data = np.array([], dtype=np.float32)
        self.audio_duration_ms = 0
        
        # 網頁級別的平滑緩衝區
        self.fft_smoothed = np.zeros(self.BAR_COUNT)
        self.smoothed_values = np.zeros(self.BAR_COUNT)
        self.peaks = np.zeros(self.BAR_COUNT)
        self.osc_smoothed = np.zeros(self.FFT_SIZE, dtype=np.float32)
        
        self.scroll_y = 0
        self.toggle_btn_rect = pygame.Rect(self.sidebar_w, 20, 35, 45)
        self.is_loading = False

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
            # 強制轉單聲道、標準44100Hz
            audio = audio.set_channels(1).set_frame_rate(44100)
            raw = audio.get_array_of_samples()
            
            # 轉換為標準浮點數數值 (-1.0 到 1.0)
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

    def run(self):
        running = True
        while running:
            mx, my = pygame.mouse.get_pos()
            
            self.sidebar_w = int(self.sidebar_w * 0.8 + self.sidebar_target_w * 0.2)
            if abs(self.sidebar_w - self.sidebar_target_w) < 2:
                self.sidebar_w = self.sidebar_target_w
            self.toggle_btn_rect.x = self.sidebar_w + 5

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1: 
                        if self.toggle_btn_rect.collidepoint(mx, my):
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

            # --- CANVAS RENDERING ---
            self.screen.fill(self.BG_DARK)
            
            current_ms = pygame.mixer.music.get_pos()
            playing = pygame.mixer.music.get_busy() and current_ms >= 0
            
            raw_freq_bars = np.zeros(self.BAR_COUNT, dtype=np.float32)
            time_data = np.zeros(self.FFT_SIZE, dtype=np.float32)
            
            if playing and self.np_audio_data.size > 0:
                sample_idx = int((current_ms / 1000.0) * 44100)
                if sample_idx + self.FFT_SIZE < self.np_audio_data.size:
                    time_data = self.np_audio_data[sample_idx : sample_idx + self.FFT_SIZE]
                else:
                    time_data = self.np_audio_data[sample_idx:]
                    time_data = np.pad(time_data, (0, self.FFT_SIZE - len(time_data)), 'constant')
                
                # 示波器時域超流暢平滑
                self.osc_smoothed = self.osc_smoothed * 0.4 + time_data * 0.6
                
                # 🌟 修正 1：加 Hanning 窗，並給予 2.0 倍網頁級能量補償
                windowed_time = time_data * np.hanning(self.FFT_SIZE) * 2.0
                fft_res = np.fft.rfft(windowed_time)
                # 歸一化振幅譜
                fft_abs = np.abs(fft_res) / self.FFT_SIZE
                
                # 🌟 修正 2：重新設計完美契合 4:3:3 的非線性頻帶映射 (Log-Frequency Binning)
                # 剔除 0-40Hz 的極端悶音，將最高頻鎖定在 16000Hz 內（這是樂器高頻動態最瘋狂的區間，拒絕採樣空白）
                min_hz = 42.0
                max_hz = 15500.0
                
                for i in range(self.BAR_COUNT):
                    # 使用網頁端標準的對數分布計算每一個長條對應的音訊頻率
                    pct = i / (self.BAR_COUNT - 1)
                    target_hz = min_hz * math.pow(max_hz / min_hz, pct)
                    
                    # 頻率轉成 FFT 陣列索引 (Index)
                    bin_idx = target_hz * self.FFT_SIZE / 44100.0
                    idx0 = max(0, min(self.FFT_SIZE // 2, math.floor(bin_idx)))
                    idx1 = max(0, min(self.FFT_SIZE // 2, math.ceil(bin_idx)))
                    t = bin_idx - idx0
                    
                    # 線性插值取得該頻段的原始振幅
                    amplitude = fft_abs[idx0] * (1.0 - t) + fft_abs[idx1] * t
                    
                    # 🌟 修正 3：完美復刻 Web Audio API 的分貝壓限公式 
                    # 轉換為 dB，並精準映射到 -95dB (靜音) 至 -25dB (大音量滿載) 區間
                    db = 20.0 * math.log10(amplitude + 1e-6)
                    normalized_val = (db + 95.0) / (95.0 - 25.0) # 縮放到 0.0 ~ 1.0
                    raw_freq_bars[i] = max(0.0, min(1.0, normalized_val)) * 255.0

                # 🌟 修正 4：網頁級的獨立頻段平滑係數 (SmoothingTimeConstant)
                # 原本一整組 0.85 太死板了！我們讓低頻沉穩，中高頻「完全釋放速度」！
                for i in range(self.BAR_COUNT):
                    pct = i / (self.BAR_COUNT - 1)
                    # 低頻平滑度 0.82，高頻平滑度下調至 0.55 (高頻反應速度暴增，徹底擺脫矮牆)
                    stc = 0.82 - (pct * 0.27)
                    self.fft_smoothed[i] = self.fft_smoothed[i] * stc + raw_freq_bars[i] * (1.0 - stc)
            else:
                self.fft_smoothed *= 0.7
                self.osc_smoothed *= 0.7

            # --- 1. RENDER SPECTRUM BARS (4:3:3 高度比例完美釋放) ---
            b_width = 6
            b_gap = 9
            total_w = (b_width + b_gap) * self.BAR_COUNT
            start_x = self.sidebar_w + ((self.WIDTH - self.sidebar_w) - total_w) // 2
            panel_y = self.HEIGHT 
            
            for i in range(self.BAR_COUNT):
                progress = i / (self.BAR_COUNT - 1)
                
                # 基於你的 4:3:3 視覺美學，為中高頻注入更強的動能乘數（Multiplier）
                if progress < 0.40:     # 低頻 (佔 4/10 高度空間) -> 最大高度 ~460
                    power_exponent = 2.2
                    height_multiplier = 480
                elif progress < 0.70:   # 中頻 (佔 3/10 高度空間) -> 最大高度 ~350
                    power_exponent = 2.0
                    height_multiplier = 350
                else:                   # 高頻 (佔 3/10 高度空間) -> 給予 1.1 的增益，讓高頻瘋狂彈跳
                    power_exponent = 1.4
                    height_multiplier = 350 * 1.1
                    
                # 計算最終渲染高度
                norm_val = self.fft_smoothed[i] / 255.0
                val = (norm_val ** power_exponent) * height_multiplier
                
                # 凌厲的緩衝過渡
                if val > self.smoothed_values[i]:
                    self.smoothed_values[i] = self.smoothed_values[i] * 0.1 + val * 0.9
                else:
                    # 根據頻段決定下墜速度，中高頻掉落更快、更有打擊顆粒感
                    fall_speed = 20 if progress < 0.4 else 50
                    self.smoothed_values[i] = max(0.0, self.smoothed_values[i] - fall_speed)
                    
                if self.smoothed_values[i] > self.peaks[i]:
                    self.peaks[i] = self.smoothed_values[i]
                else:
                    self.peaks[i] = max(0.0, self.peaks[i] - 7)
                    
                current_x = start_x + i * (b_width + b_gap)
                bar_h = int(self.smoothed_values[i])
                
                if bar_h > 0:
                    pygame.draw.rect(self.screen, self.NEON_CYAN, (current_x, panel_y - bar_h, b_width, bar_h))
                
                if self.peaks[i] > 0:
                    p_y = panel_y - int(self.peaks[i]) - 12
                    if p_y > 0:
                        pygame.draw.rect(self.screen, (0, 135, 135), (current_x, p_y, b_width, 4))

            # --- 2. RENDER ANTI-ALIASED PURE WHITE OSCILLOSCOPE ---
            center_y_osc = 600
            amp = 180 # 放大示波器振幅，看起來更具衝擊力
            v_width = self.WIDTH - self.sidebar_w
            
            osc_points = []
            step = max(1, self.FFT_SIZE // 180) 
            for idx in range(0, self.FFT_SIZE, step):
                x = self.sidebar_w + int((idx / self.FFT_SIZE) * v_width)
                # 限制最大振幅，防止時域波形破頂
                y_offset = max(-amp, min(amp, self.osc_smoothed[idx] * amp * 1.5))
                y = int(center_y_osc + y_offset)
                osc_points.append((x, y))
                
            if len(osc_points) > 1:
                pygame.draw.lines(self.screen, self.WHITE, False, osc_points, 2)

            # --- TOP HEADER RENDERING ---
            display_title = "LOADING..." if self.is_loading else self.song_name
            txt_title = self.font_title.render(display_title, True, self.NEON_CYAN)
            self.screen.blit(txt_title, (self.sidebar_w + 80, 100))
            
            p_container_x = self.sidebar_w + int((self.WIDTH - self.sidebar_w) * 0.05)
            p_container_w = int((self.WIDTH - self.sidebar_w) * 0.90)
            pygame.draw.rect(self.screen, (40, 30, 60), (p_container_x, 40, p_container_w, 8))
            
            progress_pct = 0.0
            if playing and self.audio_duration_ms > 0:
                progress_pct = min(1.0, current_ms / self.audio_duration_ms)
            if progress_pct > 0:
                pygame.draw.rect(self.screen, self.NEON_CYAN, (p_container_x, 40, int(p_container_w * progress_pct), 8))
                
            str_curr = self.format_time(current_ms if playing else 0)
            str_total = self.format_time(self.audio_duration_ms if self.audio_duration_ms > 0 else 0)
            self.screen.blit(self.font_time.render(str_curr, True, self.NEON_CYAN), (p_container_x, 55))
            txt_t = self.font_time.render(str_total, True, self.NEON_CYAN)
            self.screen.blit(txt_t, (p_container_x + p_container_w - txt_t.get_width(), 55))

            # --- RENDER COLLAPSIBLE PLAYLIST ---
            if self.sidebar_w > 0:
                sidebar_surf = pygame.Surface((self.sidebar_w, self.HEIGHT))
                sidebar_surf.fill(self.SIDEBAR_BG)
                
                btn_rect = pygame.Rect(10, 10, self.sidebar_w - 20, 40)
                if btn_rect.width > 40:
                    pygame.draw.rect(sidebar_surf, (35, 15, 65), btn_rect, border_radius=5)
                    pygame.draw.rect(sidebar_surf, self.NEON_CYAN, btn_rect, width=2, border_radius=5)
                    txt_btn = self.font_ui.render("SELECT FOLDER", True, self.NEON_CYAN)
                    sidebar_surf.blit(txt_btn, (btn_rect.centerx - txt_btn.get_width() // 2, btn_rect.centery - txt_btn.get_height() // 2))
                
                item_y = 70 + self.scroll_y
                for i, p in enumerate(self.playlist):
                    if 40 <= item_y <= self.HEIGHT and self.sidebar_w > 40:
                        rect = pygame.Rect(10, item_y, self.sidebar_w - 20, 30)
                        is_current = (i == self.current_idx)
                        if rect.collidepoint(mx, my):
                            pygame.draw.rect(sidebar_surf, (55, 25, 95), rect, border_radius=4)
                        elif is_current:
                            pygame.draw.rect(sidebar_surf, (25, 45, 75), rect, border_radius=4)
                            
                        name_cropped = os.path.basename(p)[:24]
                        txt_item = self.font_ui.render(f"{i+1}. {name_cropped}", True, self.NEON_CYAN if is_current else self.WHITE)
                        sidebar_surf.blit(txt_item, (20, item_y + 3))
                    item_y += 35
                
                self.screen.blit(sidebar_surf, (0, 0))
                pygame.draw.line(self.screen, self.NEON_CYAN, (self.sidebar_w, 0), (self.sidebar_w, self.HEIGHT), 2)

            # Toggle Button
            pygame.draw.rect(self.screen, (35, 15, 65), self.toggle_btn_rect, border_radius=4)
            pygame.draw.rect(self.screen, self.NEON_CYAN, self.toggle_btn_rect, width=2, border_radius=4)
            arrow_str = "<" if self.sidebar_target_w == self.MAX_SIDEBAR_W else ">"
            txt_arrow = self.font_btn.render(arrow_str, True, self.NEON_CYAN)
            self.screen.blit(txt_arrow, (self.toggle_btn_rect.centerx - txt_arrow.get_width() // 2, self.toggle_btn_rect.centery - txt_arrow.get_height() // 2))

            pygame.display.flip()
            self.clock.tick(self.FPS)

        pygame.mixer.music.stop()
        pygame.quit()
        sys.exit()

if __name__ == "__main__":
    app = MusiCanApp()
    app.run()
