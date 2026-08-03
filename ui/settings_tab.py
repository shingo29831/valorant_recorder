from PyQt6.QtWidgets import QWidget, QFormLayout, QLineEdit, QComboBox, QPushButton, QMessageBox
from core.config import Config

class SettingsTab(QWidget):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        layout = QFormLayout()
        
        self.fps_input = QComboBox()
        self.fps_input.addItems(["30", "60", "120", "144"])
        self.fps_input.setCurrentText(self.config.RECORD_FPS)
        
        self.encoder_input = QComboBox()
        self.encoder_input.addItems(["h264_nvenc", "libx264", "hevc_nvenc"])
        self.encoder_input.setCurrentText(self.config.RECORD_ENCODER)
        
        self.res_input = QComboBox()
        self.res_input.addItems(["1920x1080", "2560x1440", "1280x720"])
        self.res_input.setCurrentText(self.config.RECORD_RESOLUTION)
        
        self.riot_id_input = QLineEdit(self.config.RIOT_ID)
        self.tag_line_input = QLineEdit(self.config.TAG_LINE)
        self.api_key_input = QLineEdit(self.config.API_KEY)
        
        layout.addRow("Riot ID:", self.riot_id_input)
        layout.addRow("Tag Line:", self.tag_line_input)
        layout.addRow("Henrik API Key:", self.api_key_input)
        layout.addRow("Recording FPS:", self.fps_input)
        layout.addRow("Encoder:", self.encoder_input)
        layout.addRow("Resolution:", self.res_input)
        
        save_btn = QPushButton("Save Settings")
        save_btn.clicked.connect(self.save_settings)
        layout.addRow(save_btn)
        
        self.setLayout(layout)

    def save_settings(self):
        self.config.RIOT_ID = self.riot_id_input.text()
        self.config.TAG_LINE = self.tag_line_input.text()
        self.config.API_KEY = self.api_key_input.text()
        self.config.RECORD_FPS = self.fps_input.currentText()
        self.config.RECORD_ENCODER = self.encoder_input.currentText()
        self.config.RECORD_RESOLUTION = self.res_input.currentText()
        
        self.config.save()
        QMessageBox.information(self, "Success", "Settings saved successfully. Restart app to apply changes.")