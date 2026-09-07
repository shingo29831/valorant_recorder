import os
from PyQt6.QtCore import QObject, pyqtSignal, QUrl, QTimer
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput

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
        self.audio_output.setVolume(self.current_sys_volume / 2.0)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)
        
        self.mic_player = QMediaPlayer(self)
        self.mic_audio_output = QAudioOutput(self)
        self.mic_audio_output.setVolume(self.current_mic_volume / 2.0)
        self.mic_player.setAudioOutput(self.mic_audio_output)
        
        self.media_loaded = False
        self.mic_loaded = False
        self.was_playing_before_seek = False
        self.is_seeking = False
        
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self.play)
        
        self.media_player.mediaStatusChanged.connect(self._on_media_player_status_changed)
        self.mic_player.mediaStatusChanged.connect(self._on_mic_player_status_changed)
        
        self.media_player.positionChanged.connect(self._on_position_changed)
        self.media_player.durationChanged.connect(self._on_duration_changed)
        self.media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.media_player.errorOccurred.connect(self._on_error_occurred)
        
    def _on_position_changed(self, pos):
        self.positionChanged.emit(pos)

    def _on_duration_changed(self, duration):
        self.durationChanged.emit(duration)

    def _on_error_occurred(self, error, error_string):
        self.errorOccurred.emit(error, error_string)

    def _on_media_player_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            tracks = self.media_player.audioTracks()
            if len(tracks) >= 3:
                self.media_player.setActiveAudioTrack(1)
            elif len(tracks) > 0:
                self.media_player.setActiveAudioTrack(0)
            self.media_loaded = True
            self._check_both_loaded_and_play()

    def _on_mic_player_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            tracks = self.mic_player.audioTracks()
            if len(tracks) >= 3:
                self.mic_player.setActiveAudioTrack(2)
            elif len(tracks) == 2:
                self.mic_player.setActiveAudioTrack(1)
            self.mic_loaded = True
            self._check_both_loaded_and_play()

    def _check_both_loaded_and_play(self):
        if self.media_loaded and self.mic_loaded:
            self.media_loaded = False
            self.mic_loaded = False
            self.play()
            QTimer.singleShot(150, self._restore_volume)

    def _restore_volume(self):
        self.audio_output.setVolume(self.current_sys_volume / 2.0)
        if len(self.mic_player.audioTracks()) >= 2:
            self.mic_audio_output.setVolume(self.current_mic_volume / 2.0)
        else:
            self.mic_audio_output.setVolume(0)

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
        self.audio_output.setVolume(self.current_sys_volume / 2.0)
        self._save_volume_settings()

    def set_mic_volume(self, volume_percent):
        self.current_mic_volume = volume_percent / 100.0
        self.mic_audio_output.setVolume(self.current_mic_volume / 2.0)
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
        self.was_playing_before_seek = (self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState) or self._resume_timer.isActive()
        if self.was_playing_before_seek:
            self.pause()
            self._resume_timer.stop()

    def on_seek_requested(self, position):
        # ドラッグ中は pause 状態のまま setPosition のみ行う
        self.media_player.setPosition(position)
        self.mic_player.setPosition(position)

    def on_seek_finished(self, position):
        self.is_seeking = False
        self.media_player.setPosition(position)
        self.mic_player.setPosition(position)
        if self.was_playing_before_seek:
            # シーク処理（デコード）が完了する前に再生が始まると時間が進んでしまうため、
            # わずかに遅延させてから再生を再開する
            self._resume_timer.start(150)

    def set_position_direct(self, position):
        # スキップボタンなどからの直接シーク用
        was_playing = (self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState) or self._resume_timer.isActive()
        if was_playing:
            self.pause()
            self._resume_timer.stop()
        self.media_player.setPosition(position)
        self.mic_player.setPosition(position)
        if was_playing:
            self._resume_timer.start(150)
