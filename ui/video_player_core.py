import os
from PyQt6.QtCore import QObject, pyqtSignal, QUrl, QTimer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QMediaMetaData

class VideoPlayerCore(QObject):
    positionChanged = pyqtSignal(int)
    durationChanged = pyqtSignal(int)
    playbackStateChanged = pyqtSignal(int)
    errorOccurred = pyqtSignal(QMediaPlayer.Error, str)
    
    def __init__(self, video_widget, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.video_widget = video_widget
        
        self.current_sys_volume = float(getattr(self.config, 'PLAYER_SYS_VOLUME', 1.0))
        self.current_mic_volume = float(getattr(self.config, 'PLAYER_MIC_VOLUME', 1.0))
        
        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(self.current_sys_volume)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        
        self.mic_player = QMediaPlayer(self)
        self.mic_audio_output = QAudioOutput(self)
        self.mic_audio_output.setVolume(self.current_mic_volume)
        self.mic_player.setAudioOutput(self.mic_audio_output)
        # 音声専用プレイヤーとして動作させ、映像デコードによるスタックを防止
        self.mic_player.setActiveVideoTrack(-1)
        
        self.media_loaded = False
        self.mic_loaded = False
        self.was_playing_before_seek = False
        self.is_seeking = False
        self.pending_play_after_seek = False
        
        # positionChanged が発火しなかった場合のフェールセーフ（保険）タイマー
        self._failsafe_play_timer = QTimer(self)
        self._failsafe_play_timer.setSingleShot(True)
        self._failsafe_play_timer.timeout.connect(self._force_play_if_pending)
        
        self.media_player.mediaStatusChanged.connect(self._on_media_player_status_changed)
        self.mic_player.mediaStatusChanged.connect(self._on_mic_player_status_changed)
        self.media_player.tracksChanged.connect(self._on_media_tracks_changed)
        self.mic_player.tracksChanged.connect(self._on_mic_tracks_changed)
        self.mic_player.errorOccurred.connect(self._on_mic_error_occurred)
        
        self.media_player.positionChanged.connect(self._on_position_changed)
        self.media_player.durationChanged.connect(self._on_duration_changed)
        self.media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.media_player.errorOccurred.connect(self._on_error_occurred)
        
    def _force_play_if_pending(self):
        if self.pending_play_after_seek:
            self.pending_play_after_seek = False
            self.play()

    def _on_position_changed(self, pos):
        self.positionChanged.emit(pos)
        # シーク完了を検知したら、即座に再生を再開する
        if self.pending_play_after_seek:
            self.pending_play_after_seek = False
            self._failsafe_play_timer.stop()
            self.play()

    def _on_duration_changed(self, duration):
        self.durationChanged.emit(duration)

    def _on_error_occurred(self, error, error_string):
        self.errorOccurred.emit(error, error_string)

    def _on_mic_error_occurred(self, error, error_string):
        pass

    def _apply_audio_tracks(self):
        # 3トラック動画 (標準/過去録画): Track 0=Mix, Track 1=System, Track 2=Mic
        # 2トラック動画: Track 0=System, Track 1=Mic
        # 1トラック動画: Track 0=Mix/System
        media_tracks = self.media_player.audioTracks()
        num_tracks = len(media_tracks)

        if num_tracks >= 3:
            sys_idx = 1
            mic_idx = 2
            # メタデータ(タイトル/言語)が存在する場合は優先判別
            for idx, meta in enumerate(media_tracks):
                try:
                    title = str(meta.value(QMediaMetaData.Key.Title) or "").lower()
                    lang = str(meta.value(QMediaMetaData.Key.Language) or "").lower()
                    if "sys" in title or lang == "eng":
                        sys_idx = idx
                    elif "mic" in title or lang == "jpn":
                        mic_idx = idx
                except Exception:
                    pass

            self.media_player.setActiveAudioTrack(sys_idx)
            self.mic_player.setActiveAudioTrack(mic_idx)
        elif num_tracks == 2:
            sys_idx = 0
            mic_idx = 1
            for idx, meta in enumerate(media_tracks):
                try:
                    title = str(meta.value(QMediaMetaData.Key.Title) or "").lower()
                    lang = str(meta.value(QMediaMetaData.Key.Language) or "").lower()
                    if "sys" in title or lang == "eng":
                        sys_idx = idx
                    elif "mic" in title or lang == "jpn":
                        mic_idx = idx
                except Exception:
                    pass

            self.media_player.setActiveAudioTrack(sys_idx)
            self.mic_player.setActiveAudioTrack(mic_idx)
        elif num_tracks == 1:
            self.media_player.setActiveAudioTrack(0)
            self.mic_player.setActiveAudioTrack(-1)

    def _on_media_tracks_changed(self):
        self._apply_audio_tracks()
        self._restore_volume()

    def _on_mic_tracks_changed(self):
        self.mic_player.setActiveVideoTrack(-1)
        self._apply_audio_tracks()
        self._restore_volume()

    def _on_media_player_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self._apply_audio_tracks()
            self.media_loaded = True
            self._check_both_loaded_and_play()

    def _on_mic_player_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            self.mic_player.setActiveVideoTrack(-1)
            self._apply_audio_tracks()
            self.mic_loaded = True
            self._check_both_loaded_and_play()

    def _check_both_loaded_and_play(self):
        if self.media_loaded and self.mic_loaded:
            self.media_loaded = False
            self.mic_loaded = False
            self.play()
            QTimer.singleShot(150, self._restore_volume)

    def _restore_volume(self):
        self.audio_output.setVolume(self.current_sys_volume)
        tracks = self.mic_player.audioTracks()
        # 1トラックのみ（過去ファイル等）の場合は重複再生を防止し、それ以外は設定値を復帰
        if len(tracks) == 1:
            self.mic_audio_output.setVolume(0)
        else:
            self.mic_audio_output.setVolume(self.current_mic_volume)

    def _on_playback_state_changed(self, state):
        self.playbackStateChanged.emit(state.value)

    def load_source(self, file_path):
        self.stop()
        self.media_loaded = False
        self.mic_loaded = False
        
        self.audio_output.setVolume(0)
        self.mic_audio_output.setVolume(0)
        
        if file_path and os.path.exists(file_path):
            url = QUrl.fromLocalFile(os.path.abspath(file_path))
            self.media_player.setSource(url)
            self.mic_player.setSource(url)
            self.mic_player.setActiveVideoTrack(-1)
        else:
            self.media_player.setSource(QUrl())
            self.mic_player.setSource(QUrl())

    def play(self):
        self.media_player.play()
        self.mic_player.play()

    def pause(self):
        self.media_player.pause()
        self.mic_player.pause()

    def stop(self):
        self.media_player.stop()
        self.mic_player.stop()

    def toggle_play(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.pause()
        else:
            self.play()

    def set_sys_volume(self, volume_percent):
        self.current_sys_volume = volume_percent / 100.0
        self.audio_output.setVolume(self.current_sys_volume)
        self._save_volume_settings()

    def set_mic_volume(self, volume_percent):
        self.current_mic_volume = volume_percent / 100.0
        tracks = self.mic_player.audioTracks()
        if len(tracks) == 1:
            self.mic_audio_output.setVolume(0)
        else:
            self.mic_audio_output.setVolume(self.current_mic_volume)
        self._save_volume_settings()

    def _save_volume_settings(self):
        self.config.PLAYER_SYS_VOLUME = self.current_sys_volume
        self.config.PLAYER_MIC_VOLUME = self.current_mic_volume
        self.config.save()

    def set_playback_rate(self, rate):
        self.media_player.setPlaybackRate(rate)
        self.mic_player.setPlaybackRate(rate)

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

    def on_seek_finished(self, position):
        self.is_seeking = False
        if self.was_playing_before_seek:
            self.pending_play_after_seek = True
            self._failsafe_play_timer.start(500)
        self.media_player.setPosition(position)
        self.mic_player.setPosition(position)

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
