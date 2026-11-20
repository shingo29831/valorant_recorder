import os
from PyQt6.QtCore import QObject, pyqtSignal, QUrl, QTimer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaMetaData

class VideoPlayerCore(QObject):
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    playbackStateChanged = pyqtSignal(int)
    audioTracksChanged = pyqtSignal(int)
    errorOccurred = pyqtSignal(QMediaPlayer.Error, str)
    
    def __init__(self, video_widget, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.video_widget = video_widget
        self.current_playback_rate = 1.0
        
        self.current_sys_volume = float(getattr(self.config, 'PLAYER_SYS_VOLUME', 1.0))
        self.current_mic_volume = float(getattr(self.config, 'PLAYER_MIC_VOLUME', 1.0))
        self.current_chat_volume = float(getattr(self.config, 'PLAYER_CHAT_VOLUME', 1.0))
        
        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(min(1.0, self.current_sys_volume))
        self.media_player.setAudioOutput(self.audio_output)
        # 画面遷移完了後のload_source時に接続し、未表示のネイティブサーフェス先行描画を防止
        
        self.mic_player = QMediaPlayer(self)
        self.mic_audio_output = QAudioOutput(self)
        # トラック分離確立までミュートし位相干渉・二重再生を防止
        self.mic_audio_output.setVolume(0.0)
        self.mic_player.setAudioOutput(self.mic_audio_output)
        self.mic_player.setActiveVideoTrack(-1)

        # 3トラック動画検知時のみ遅延ロードし、既存2トラック動画への負荷・挙動影響をゼロに保つ
        self.chat_player = QMediaPlayer(self)
        self.chat_audio_output = QAudioOutput(self)
        self.chat_audio_output.setVolume(0.0)
        self.chat_player.setAudioOutput(self.chat_audio_output)
        self.chat_player.setActiveVideoTrack(-1)
        
        for player in (self.media_player, self.mic_player, self.chat_player):
            if hasattr(player, 'setPitchCompensation'):
                try:
                    player.setPitchCompensation(True)
                except Exception:
                    pass
        
        self._current_file_url = None
        self._has_chat_track = False
        self.media_loaded = False
        self.mic_loaded = False
        self.chat_loaded = True
        self.auto_play_on_load = False
        self.was_playing_before_seek = False
        self.is_seeking = False
        self.pending_play_after_seek = False
        self._is_unloading = False
        
        # positionChanged が発火しなかった場合のフェールセーフ（保険）タイマー
        self._failsafe_play_timer = QTimer(self)
        self._failsafe_play_timer.setSingleShot(True)
        self._failsafe_play_timer.timeout.connect(self._force_play_if_pending)
        
        self.media_player.mediaStatusChanged.connect(self._on_media_player_status_changed)
        self.mic_player.mediaStatusChanged.connect(self._on_mic_player_status_changed)
        self.chat_player.mediaStatusChanged.connect(self._on_chat_player_status_changed)
        self.media_player.tracksChanged.connect(self._on_media_tracks_changed)
        self.mic_player.tracksChanged.connect(self._on_mic_tracks_changed)
        self.chat_player.tracksChanged.connect(self._on_chat_tracks_changed)
        self.mic_player.errorOccurred.connect(self._on_mic_error_occurred)
        self.chat_player.errorOccurred.connect(self._on_chat_error_occurred)
        
        self.media_player.positionChanged.connect(self._on_position_changed)
        self.media_player.durationChanged.connect(self._on_duration_changed)
        self.media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.media_player.errorOccurred.connect(self._on_error_occurred)
        
    def _force_play_if_pending(self):
        if self.pending_play_after_seek:
            self.pending_play_after_seek = False
            self.play()

    def _on_position_changed(self, pos):
        if self._is_unloading:
            return
        self.positionChanged.emit(pos)
        # シーク完了を検知したら、即座に再生を再開する
        if self.pending_play_after_seek:
            self.pending_play_after_seek = False
            self._failsafe_play_timer.stop()
            self.play()
        elif self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            if (self.mic_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and
                    abs(self.mic_player.position() - pos) > 60):
                self.mic_player.setPosition(pos)
            if (self._has_chat_track and
                    self.chat_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState and
                    abs(self.chat_player.position() - pos) > 60):
                self.chat_player.setPosition(pos)

    def _on_duration_changed(self, duration):
        if self._is_unloading:
            return
        self.durationChanged.emit(duration)

    def _on_error_occurred(self, error, error_string):
        if self._is_unloading:
            return
        self.errorOccurred.emit(error, error_string)

    def _on_mic_error_occurred(self, error, error_string):
        if not self._is_unloading and not self.mic_loaded:
            self.mic_loaded = True
            self._check_both_loaded_and_play()

    def _on_chat_error_occurred(self, error, error_string):
        if not self._is_unloading and not self.chat_loaded:
            self.chat_loaded = True
            self._check_both_loaded_and_play()

    def _ensure_chat_player_loaded(self):
        if self._has_chat_track or self._current_file_url is None:
            return
        if len(self.media_player.audioTracks()) < 3:
            return
        self._has_chat_track = True
        self.chat_loaded = False
        self.chat_audio_output.setVolume(0.0)
        self.chat_player.setPlaybackRate(self.current_playback_rate)
        self.chat_player.setSource(self._current_file_url)
        if len(self.chat_player.videoTracks()) > 0:
            self.chat_player.setActiveVideoTrack(-1)

    def _is_mic_track_ready(self) -> bool:
        return (
            len(self.mic_player.audioTracks()) >= 2
            and self.mic_player.activeAudioTrack() == 1
            and self.current_mic_volume > 0
        )

    def _is_chat_track_ready(self) -> bool:
        return (
            self._has_chat_track
            and len(self.chat_player.audioTracks()) >= 3
            and self.chat_player.activeAudioTrack() == 2
            and self.current_chat_volume > 0
        )

    def _apply_output_volumes(self):
        # Qt6のQAudioOutputは1.0上限でクランプされるため、101%〜200%設定時は自動ヘッドルーム正規化で相対比を維持
        mic_active = self._is_mic_track_ready()
        chat_active = self._is_chat_track_ready()

        peak = max(1.0, self.current_sys_volume)
        if mic_active:
            peak = max(peak, self.current_mic_volume)
        if chat_active:
            peak = max(peak, self.current_chat_volume)

        self.audio_output.setVolume(max(0.0, min(1.0, self.current_sys_volume / peak)))
        self.mic_audio_output.setVolume(
            max(0.0, min(1.0, self.current_mic_volume / peak)) if mic_active else 0.0
        )
        self.chat_audio_output.setVolume(
            max(0.0, min(1.0, self.current_chat_volume / peak)) if chat_active else 0.0
        )

    def _apply_audio_tracks(self):
        # 完全分離マルチトラック構成: Track 0 = システム音声, Track 1 = マイク, Track 2 = チャット音声
        media_tracks = self.media_player.audioTracks()
        mic_tracks = self.mic_player.audioTracks()
        
        if len(media_tracks) >= 1:
            self.media_player.setActiveAudioTrack(0)

        if len(mic_tracks) >= 2:
            self.mic_player.setActiveAudioTrack(1)
        elif len(mic_tracks) == 1:
            self.mic_player.setActiveAudioTrack(-1)

        if self._has_chat_track:
            chat_tracks = self.chat_player.audioTracks()
            if len(chat_tracks) >= 3:
                self.chat_player.setActiveAudioTrack(2)
            elif len(chat_tracks) >= 1:
                self.chat_player.setActiveAudioTrack(-1)

    def _on_media_tracks_changed(self):
        if self._is_unloading or self.media_player.source().isEmpty():
            return
        self._apply_audio_tracks()
        self._ensure_chat_player_loaded()
        self.audioTracksChanged.emit(len(self.media_player.audioTracks()))
        self._restore_volume()

    def _on_mic_tracks_changed(self):
        if self._is_unloading or self.mic_player.source().isEmpty():
            return
        if len(self.mic_player.videoTracks()) > 0:
            self.mic_player.setActiveVideoTrack(-1)
        self._apply_audio_tracks()
        self._restore_volume()

    def _on_chat_tracks_changed(self):
        if self._is_unloading or not self._has_chat_track or self.chat_player.source().isEmpty():
            return
        if len(self.chat_player.videoTracks()) > 0:
            self.chat_player.setActiveVideoTrack(-1)
        self._apply_audio_tracks()
        self._restore_volume()

    def _on_media_player_status_changed(self, status):
        if self._is_unloading:
            return
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self._apply_audio_tracks()
            self._ensure_chat_player_loaded()
            self.audioTracksChanged.emit(len(self.media_player.audioTracks()))
            self.media_loaded = True
            self._check_both_loaded_and_play()

    def _on_mic_player_status_changed(self, status):
        if self._is_unloading:
            return
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self.mic_player.setActiveVideoTrack(-1)
            self._apply_audio_tracks()
            self.mic_loaded = True
            self._check_both_loaded_and_play()

    def _on_chat_player_status_changed(self, status):
        if self._is_unloading or not self._has_chat_track:
            return
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self.chat_player.setActiveVideoTrack(-1)
            self._apply_audio_tracks()
            self.chat_player.setPlaybackRate(self.current_playback_rate)
            self.chat_loaded = True
            if self.auto_play_on_load:
                self._check_both_loaded_and_play()
            else:
                self._restore_volume()

    def _check_both_loaded_and_play(self):
        chat_ready = (not self._has_chat_track) or self.chat_loaded
        if self.auto_play_on_load and self.media_loaded and self.mic_loaded and chat_ready:
            self.auto_play_on_load = False
            self.media_loaded = False
            self.mic_loaded = False
            self.chat_loaded = not self._has_chat_track
            self._apply_audio_tracks()
            self.set_playback_rate(self.current_playback_rate)
            self.play()
            QTimer.singleShot(150, self._restore_volume)

    def _restore_volume(self):
        if self.media_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        self._apply_audio_tracks()
        main_pos = self.media_player.position()

        # Track 1が確立され、かつ音量指定がある場合のみ同期して復帰（二重再生とクロック競合を防止）
        if self._is_mic_track_ready():
            if abs(self.mic_player.position() - main_pos) > 30:
                self.mic_player.setPosition(main_pos)
            if self.mic_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self.mic_player.play()
        elif self.mic_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.mic_player.pause()

        if self._is_chat_track_ready():
            if abs(self.chat_player.position() - main_pos) > 30:
                self.chat_player.setPosition(main_pos)
            if self.chat_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self.chat_player.play()
        elif self._has_chat_track and self.chat_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.chat_player.pause()

        self._apply_output_volumes()

    def _on_playback_state_changed(self, state):
        self.playbackStateChanged.emit(state.value)

    def load_source(self, file_path):
        self.stop()
        self._current_file_url = None
        self._has_chat_track = False
        self.media_loaded = False
        self.mic_loaded = False
        self.chat_loaded = True
        self.auto_play_on_load = False
        
        self.audio_output.setVolume(0)
        self.mic_audio_output.setVolume(0)
        self.chat_audio_output.setVolume(0)
        if not self.chat_player.source().isEmpty():
            self.chat_player.setSource(QUrl())
        
        if file_path and os.path.exists(file_path):
            if self.media_player.videoOutput() != self.video_widget:
                self.media_player.setVideoOutput(self.video_widget)
            self.auto_play_on_load = True
            url = QUrl.fromLocalFile(os.path.abspath(file_path))
            self._current_file_url = url
            self.media_player.setSource(url)
            self.mic_player.setSource(url)
            if len(self.mic_player.videoTracks()) > 0:
                self.mic_player.setActiveVideoTrack(-1)
        else:
            self.unload()

    def unload(self):
        if self._is_unloading:
            return
        self._is_unloading = True
        try:
            self.stop()
            self._current_file_url = None
            self._has_chat_track = False
            # 非表示・リサイズ時のD3D11スワップチェーン競合を防ぐため映像出力を先に切り離す
            if self.media_player.videoOutput() is not None:
                self.media_player.setVideoOutput(None)
            if not self.media_player.source().isEmpty():
                self.media_player.setSource(QUrl())
            if not self.mic_player.source().isEmpty():
                self.mic_player.setSource(QUrl())
            if not self.chat_player.source().isEmpty():
                self.chat_player.setSource(QUrl())
        finally:
            self._is_unloading = False

    def play(self):
        self._apply_audio_tracks()
        # 末尾到達後の再再生や一時停止再開時のトラック間ズレを防ぐため開始前に位置を揃える
        if self.media_player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
            target_pos = 0
            self.media_player.setPosition(0)
        else:
            target_pos = self.media_player.position()

        mic_ready = self._is_mic_track_ready()
        chat_ready = self._is_chat_track_ready()
        if mic_ready and abs(self.mic_player.position() - target_pos) > 15:
            self.mic_player.setPosition(target_pos)
        if chat_ready and abs(self.chat_player.position() - target_pos) > 15:
            self.chat_player.setPosition(target_pos)

        self._apply_output_volumes()
        self.media_player.play()

        # 音量が0またはトラック未確立時は補助プレイヤーを動かさずクロック競合を防止
        if mic_ready:
            self.mic_player.play()
        else:
            self.mic_player.pause()

        if self._has_chat_track:
            if chat_ready:
                self.chat_player.play()
            else:
                self.chat_player.pause()

    def pause(self):
        self.auto_play_on_load = False
        self.media_player.pause()
        self.mic_player.pause()
        if self._has_chat_track:
            self.chat_player.pause()

    def stop(self):
        self.auto_play_on_load = False
        self.media_loaded = False
        self.mic_loaded = False
        self.chat_loaded = not self._has_chat_track
        self.pending_play_after_seek = False
        self._failsafe_play_timer.stop()
        self.audio_output.setVolume(0.0)
        self.mic_audio_output.setVolume(0.0)
        self.chat_audio_output.setVolume(0.0)
        if self.media_player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            self.media_player.stop()
        if self.mic_player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            self.mic_player.stop()
        if self.chat_player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            self.chat_player.stop()

    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.pause()
        else:
            self.play()

    def set_sys_volume(self, volume_percent):
        self.current_sys_volume = volume_percent / 100.0
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._apply_output_volumes()
        else:
            self.audio_output.setVolume(min(1.0, self.current_sys_volume))
        self._save_volume_settings()

    def set_mic_volume(self, volume_percent):
        self.current_mic_volume = volume_percent / 100.0
        self._apply_audio_tracks()
        if not self._is_mic_track_ready():
            self.mic_player.pause()
        elif self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            if abs(self.mic_player.position() - self.media_player.position()) > 30:
                self.mic_player.setPosition(self.media_player.position())
            self.mic_player.play()
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._apply_output_volumes()
        else:
            self.mic_audio_output.setVolume(0.0)
        self._save_volume_settings()

    def set_chat_volume(self, volume_percent):
        self.current_chat_volume = volume_percent / 100.0
        if self._has_chat_track:
            self._apply_audio_tracks()
            if not self._is_chat_track_ready():
                self.chat_player.pause()
            elif self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                if abs(self.chat_player.position() - self.media_player.position()) > 30:
                    self.chat_player.setPosition(self.media_player.position())
                self.chat_player.play()
            if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._apply_output_volumes()
            else:
                self.chat_audio_output.setVolume(0.0)
        self._save_volume_settings()

    def _save_volume_settings(self):
        self.config.PLAYER_SYS_VOLUME = self.current_sys_volume
        self.config.PLAYER_MIC_VOLUME = self.current_mic_volume
        self.config.PLAYER_CHAT_VOLUME = self.current_chat_volume
        self.config.save()

    def set_playback_rate(self, rate):
        self.current_playback_rate = rate
        self.media_player.setPlaybackRate(rate)
        self.mic_player.setPlaybackRate(rate)
        if self._has_chat_track:
            self.chat_player.setPlaybackRate(rate)

    def position(self):
        return self.media_player.position()

    def duration(self):
        return self.media_player.duration()

    def audio_track_count(self):
        return len(self.media_player.audioTracks())

    def on_seek_started(self):
        self.is_seeking = True
        self.was_playing_before_seek = (self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState) or self.pending_play_after_seek
        if self.was_playing_before_seek:
            self.pause()
            self.pending_play_after_seek = False
            self._failsafe_play_timer.stop()

    def on_seek_requested(self, position):
        # ドラッグ中は pause 状態のまま setPosition のみ行う
        self.media_player.setPosition(position)
        self.mic_player.setPosition(position)
        if self._has_chat_track:
            self.chat_player.setPosition(position)

    def on_seek_finished(self, position):
        self.is_seeking = False
        if self.was_playing_before_seek:
            self.pending_play_after_seek = True
            self._failsafe_play_timer.start(500)
        self.media_player.setPosition(position)
        self.mic_player.setPosition(position)
        if self._has_chat_track:
            self.chat_player.setPosition(position)

    def set_position_direct(self, position):
        # スキップボタンなどからの直接シーク用
        was_playing = (self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState) or self.pending_play_after_seek
        if was_playing:
            self.pause()
            self.pending_play_after_seek = False
            self._failsafe_play_timer.stop()
            
        if was_playing:
            self.pending_play_after_seek = True
            self._failsafe_play_timer.start(500)
            
        self.media_player.setPosition(position)
        self.mic_player.setPosition(position)
        if self._has_chat_track:
            self.chat_player.setPosition(position)
