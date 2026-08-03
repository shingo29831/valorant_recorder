import urllib.request
import json
import urllib.error
import urllib.parse

def fetch_recent_match_timeline(region: str, name: str, tag: str, size: int = 3):
    print(f"[{name}#{tag}] の直近 {size} 試合のデータを取得中...\n")
    
    # HenrikDev API v3 を使用して直近の試合を取得
    safe_name = urllib.parse.quote(name)
    safe_tag = urllib.parse.quote(tag)
    url = f"https://api.henrikdev.xyz/valorant/v3/matches/{region}/{safe_name}/{safe_tag}?size={size}"
    
    # APIキーをヘッダーにセット
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Authorization': API_KEY
    }
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            if data['status'] != 200 or not data['data']:
                print("試合データが見つかりませんでした。")
                return

            for index, match in enumerate(data['data']):
                metadata = match['metadata']
                players = match['players']['all_players']
                teams = match.get('teams', {}) # デスマッチなどチーム情報がない場合を考慮

                # 自分のプレイヤー情報を特定
                my_player_data = None
                for player in players:
                    if player['name'].lower() == name.lower() and player['tag'].lower() == tag.lower():
                        my_player_data = player
                        break
                
                if not my_player_data:
                    print(f"=== 試合 {index + 1} の自分のデータが見つかりませんでした ===")
                    continue

                my_puuid = my_player_data['puuid']
                my_team = my_player_data.get('team')
                my_stats = my_player_data['stats']
                my_agent = my_player_data['character']

                # 試合結果とスコアを算出
                score_display = "N/A"
                result_display = "N/A (非チームモード)"
                if teams and 'red' in teams and 'blue' in teams:
                    red_score = teams['red'].get('rounds_won', 0)
                    blue_score = teams['blue'].get('rounds_won', 0)
                    score_display = f"{red_score} - {blue_score}"

                    # 自分のチームの勝敗を判定
                    my_team_data = teams.get(my_team.lower(), {})
                    if my_team_data.get('has_won') is True:
                        result_display = "勝利"
                    elif my_team_data.get('has_won') is False:
                        result_display = "敗北"
                    elif red_score == blue_score:
                        result_display = "引き分け"
                    else:
                        result_display = "不明"

                # 試合時間を分秒に変換
                # APIから秒単位で返却されるが、仕様変更によるミリ秒返却に備えフェイルセーフを実装
                raw_length = metadata['game_length']
                if raw_length > 20000:  # 20000秒(約5.5時間)以上の場合はミリ秒と判定
                    game_length_sec = raw_length // 1000
                else:
                    game_length_sec = raw_length

                game_minutes = game_length_sec // 60
                game_seconds = game_length_sec % 60

                print(f"=== 試合 {index + 1} 基本情報 ===")
                print(f"マップ       : {metadata['map']}")
                print(f"モード       : {metadata['mode']}")
                print(f"試合結果     : {result_display} (スコア: {score_display})")
                print(f"エージェント : {my_agent}")
                print(f"KDA          : {my_stats['kills']}/{my_stats['deaths']}/{my_stats['assists']}")
                print(f"試合開始時刻 : {metadata['game_start_patched']}")
                print(f"試合時間     : {game_minutes}分 {game_seconds}秒")
                print("====================\n")

                print("=== タイムライン（ハイライト） ===")
                if match.get('rounds'):
                    for round_index, round_data in enumerate(match['rounds']):
                        round_num = round_index + 1
                        end_type = round_data.get('end_type', 'Unknown')
                        winning_team = round_data.get('winning_team', 'Unknown')
                        print(f"[ラウンド {round_num}] 勝者: {winning_team} (理由: {end_type})")
                else:
                    print("ラウンド情報なし (デスマッチ等のモード)")

                print("\n=== キル・アシストイベント (自分に関するもののみ抜粋) ===")
                event_count = 0
                for kill in match.get('kills', []):
                    kill_time_sec = kill['kill_time_in_match'] // 1000
                    minutes = kill_time_sec // 60
                    seconds = kill_time_sec % 60
                    
                    victim_name = kill.get('victim_display_name', 'Unknown')
                    killer_name = kill.get('killer_display_name', 'Unknown')
                    weapon = kill.get('damage_weapon_name', 'Unknown')
                    
                    # 自分がキルした場合
                    if kill.get('killer_puuid') == my_puuid:
                        print(f"[{minutes:02d}:{seconds:02d}] 💥キル -> {victim_name} (武器: {weapon})")
                        event_count += 1
                    else:
                        # 自分がアシストした場合
                        assistants = kill.get('assistants', [])
                        for assistant in assistants:
                            if assistant.get('assistant_puuid') == my_puuid:
                                print(f"[{minutes:02d}:{seconds:02d}] 🤝アシスト -> {victim_name} (キルした味方: {killer_name})")
                                event_count += 1
                                break
                
                if event_count == 0:
                    print("キル・アシストはありませんでした。")
                
                print("\n" + "*" * 50 + "\n")

    except urllib.error.HTTPError as e:
        print(f"APIエラー: {e.code} - {e.reason}")
        print("※ レート制限、またはアカウントが非公開設定になっている可能性があります。")
    except Exception as e:
        print(f"予期せぬエラー: {e}")

if __name__ == "__main__":
    # 開発メモ: ご自身のValorantのRiot IDとタグラインに変更して実行してください
    REGION = "ap"
    RIOT_ID = "shingo"
    TAG_LINE = "7445"
    
    # 取得したHenrikDev APIキーをここに貼り付けます
    API_KEY = "HDEV-2cc41137-127c-41e1-a60e-7dcc90ab0739"
    
    if API_KEY.startswith("HDEV-xxx"):
        print("スクリプト内の API_KEY を取得したものに変更してから実行してください。")
    else:
        # 取得したい試合数を指定して実行 (例: 3試合)
        fetch_recent_match_timeline(REGION, RIOT_ID, TAG_LINE, size=4)