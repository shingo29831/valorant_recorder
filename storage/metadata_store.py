import json
import os
from datetime import datetime

class MetadataStore:
    def __init__(self, save_dir: str):
        self.save_dir = save_dir
        os.makedirs(self.save_dir, exist_ok=True)

    def save_match_metadata(self, match_data: dict, mmr_change: int) -> str:
        match_id = match_data['metadata']['matchid']
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"match_{timestamp}_{match_id}.json"
        filepath = os.path.join(self.save_dir, filename)

        payload = {
            "match_info": match_data,
            "mmr_change": mmr_change,
            "recorded_at": timestamp
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=4, ensure_ascii=False)
        
        return filepath