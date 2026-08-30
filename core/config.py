import os
from dotenv import load_dotenv, set_key

class Config:
    def __init__(self):
        self.env_file = ".env"
        load_dotenv(self.env_file)
        self.LANGUAGE = os.environ.get("LANGUAGE", "ja")
        self.REGION = os.environ.get("VALORANT_REGION", "ap")
        self.RIOT_ID = os.environ.get("RIOT_ID", "")
        self.TAG_LINE = os.environ.get("TAG_LINE", "")
        self.SAVE_DIR = os.environ.get("RECORD_SAVE_DIR", "./records")
        
        self.RECORD_VIDEO_FORMAT = os.environ.get("RECORD_VIDEO_FORMAT", "ddagrab")
        self.RECORD_INPUT_SOURCE = os.environ.get("RECORD_INPUT_SOURCE", "desktop")
        self.RECORD_AUDIO_SYSTEM = os.environ.get("RECORD_AUDIO_SYSTEM", "default")
        self.RECORD_AUDIO_SYSTEM_GAIN = os.environ.get("RECORD_AUDIO_SYSTEM_GAIN", "1.0")
        self.RECORD_AUDIO_MIC = os.environ.get("RECORD_AUDIO_MIC", "")
        self.RECORD_AUDIO_MIC_GAIN = os.environ.get("RECORD_AUDIO_MIC_GAIN", "1.0")
        self.RECORD_AUDIO_MIC_NOISE_GATE = os.environ.get("RECORD_AUDIO_MIC_NOISE_GATE", "0")
        self.RECORD_AUDIO_MIC_DENOISE = os.environ.get("RECORD_AUDIO_MIC_DENOISE", "None")
        self.RECORD_ENCODER = os.environ.get("RECORD_ENCODER", "h264_nvenc")
        self.RECORD_FPS = os.environ.get("RECORD_FPS", "60")
        self.RECORD_RESOLUTION = os.environ.get("RECORD_RESOLUTION", "1920x1080")
        self.AUTO_DELETE_DAYS = int(os.environ.get("AUTO_DELETE_DAYS", "0"))
        self.CLIP_SAVE_DIR = os.environ.get("CLIP_SAVE_DIR", os.path.join(self.SAVE_DIR, "clips"))
        self.UPDATE_API_URL = os.environ.get("UPDATE_API_URL") or "https://valoreco-api.meld-task.com/api/version"
        self.PLAYER_SYS_VOLUME = self._parse_volume(os.environ.get("PLAYER_SYS_VOLUME", "1.0"))
        self.PLAYER_MIC_VOLUME = self._parse_volume(os.environ.get("PLAYER_MIC_VOLUME", "1.0"))

    def _parse_volume(self, val_str, default=1.0):
        try:
            val = float(val_str)
            # 過去バージョンで 0~100 などの整数として保存されていた場合の互換性対応
            if val > 2.0:
                return val / 100.0
            return val
        except (ValueError, TypeError):
            return default

    def save(self):
        if not os.path.exists(self.env_file):
            open(self.env_file, 'w').close()
            
        set_key(self.env_file, "LANGUAGE", self.LANGUAGE)
        set_key(self.env_file, "VALORANT_REGION", self.REGION)
        set_key(self.env_file, "RIOT_ID", self.RIOT_ID)
        set_key(self.env_file, "TAG_LINE", self.TAG_LINE)
        set_key(self.env_file, "RECORD_SAVE_DIR", self.SAVE_DIR)
        set_key(self.env_file, "RECORD_VIDEO_FORMAT", self.RECORD_VIDEO_FORMAT)
        set_key(self.env_file, "RECORD_INPUT_SOURCE", self.RECORD_INPUT_SOURCE)
        set_key(self.env_file, "RECORD_AUDIO_SYSTEM", self.RECORD_AUDIO_SYSTEM)
        set_key(self.env_file, "RECORD_AUDIO_SYSTEM_GAIN", str(self.RECORD_AUDIO_SYSTEM_GAIN))
        set_key(self.env_file, "RECORD_AUDIO_MIC", self.RECORD_AUDIO_MIC)
        set_key(self.env_file, "RECORD_AUDIO_MIC_GAIN", str(self.RECORD_AUDIO_MIC_GAIN))
        set_key(self.env_file, "RECORD_AUDIO_MIC_NOISE_GATE", str(self.RECORD_AUDIO_MIC_NOISE_GATE))
        set_key(self.env_file, "RECORD_AUDIO_MIC_DENOISE", str(self.RECORD_AUDIO_MIC_DENOISE))
        set_key(self.env_file, "RECORD_ENCODER", self.RECORD_ENCODER)
        set_key(self.env_file, "RECORD_FPS", self.RECORD_FPS)
        set_key(self.env_file, "RECORD_RESOLUTION", self.RECORD_RESOLUTION)
        set_key(self.env_file, "AUTO_DELETE_DAYS", str(self.AUTO_DELETE_DAYS))
        set_key(self.env_file, "CLIP_SAVE_DIR", self.CLIP_SAVE_DIR)
        set_key(self.env_file, "UPDATE_API_URL", self.UPDATE_API_URL)
        set_key(self.env_file, "PLAYER_SYS_VOLUME", str(self.PLAYER_SYS_VOLUME))
        set_key(self.env_file, "PLAYER_MIC_VOLUME", str(self.PLAYER_MIC_VOLUME))