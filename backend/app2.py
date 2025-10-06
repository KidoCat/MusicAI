import os
import time
import json
import urllib.parse
import requests
import google.generativeai as genai
from flask import Flask, request, jsonify
from flask_cors import CORS

# --- Flaskアプリケーションのセットアップ ---
app = Flask(__name__)
CORS(app)

# --- APIキーの設定 ---
# ★必ずご自身の有効なAmiVoice APIキーに書き換えてください
AMIVOICE_API_KEY = "" 
AMIVOICE_ENDPOINT = 'https://acp-api-async.amivoice.com/v1/recognitions'

# 環境変数からGemini APIキーを設定
try:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
except KeyError:
    print("--------------------------------------------------")
    print("エラー: 環境変数 'GEMINI_API_KEY' が設定されていません。")
    print("プログラムを実行する前に、APIキーを設定してください。")
    print("--------------------------------------------------")
    exit()

# --- Gemini API 関連の関数 ---
def create_music_prompt(emotion, words):
    """感情と言葉から、音楽生成AI用のプロンプトを作成します。"""
    if not emotion or not words:
        return "感情またはキーワードが不足しているため、プロンプトを生成できませんでした。"
        
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt_for_gemini = f"""
    あなたはプロの音楽プロデューサーです。
    提示されたテーマを基に、音楽生成AI（Suno AIなど）で利用するための、創造的で具体的なプロンプトを生成してください。

    # プロンプトに含める要素
    - ジャンル (例: Lo-fi hip hop, Ambient, Cinematic, J-Pop)
    - 雰囲気 (例: melancholy, hopeful, nostalgic)
    - 楽器 (例: gentle piano, soft synth pads, acoustic guitar)
    - テンポ (例: slow tempo, 120 BPM)
    - その他情景描写 (例: sound of gentle rain, reverb-heavy)

    # 制約条件
    - 必ず英語で、カンマ区切りの単語やフレーズで出力してください。
    - 説明文や前置きは不要です。

    ---
    ## 入力テーマ
    - 感情: {emotion}
    - キーワード: {words}

    ## 生成プロンプト
    """
    try:
        response = model.generate_content(prompt_for_gemini)
        return response.text.strip()
    except Exception as e:
        print(f"音楽プロンプトの生成中にエラー: {e}")
        return f"音楽プロンプトの生成中にエラーが発生しました: {e}"

def create_lyrics(emotion, words):
    """感情と言葉から、歌詞を生成します。"""
    if not emotion or not words:
        return "感情またはキーワードが不足しているため、歌詞を生成できませんでした。"

    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt_for_gemini = f"""
    あなたはプロの作詞家です。
    提示されたテーマを基に、リスナーの心に響くような歌詞を生成してください。

    # 歌詞の構成
    - 1番のAメロ、Bメロ、サビを作詞してください。
    - 各セクションが分かるように、[Verse 1], [Pre-Chorus], [Chorus] のような見出しを付けてください。

    # 制約条件
    - 歌詞は英語で生成してください。
    - 説明文や前置きは不要です。歌詞のみを出力してください。

    ---
    ## 入力テーマ
    - 感情: {emotion}
    - キーワード: {words}

    ## 生成される歌詞
    """
    try:
        response = model.generate_content(prompt_for_gemini)
        return response.text.strip()
    except Exception as e:
        print(f"歌詞の生成中にエラー: {e}")
        return f"歌詞の生成中にエラーが発生しました: {e}"


# --- メインのAPIエンドポイント ---
@app.route('/analyze_and_create', methods=['POST'])
def analyze_and_create():
    # --- 1. 音声ファイルを受け取る ---
    if 'audio' not in request.files:
        return jsonify({'error': '音声ファイルが見つかりません'}), 400
    
    audio_file = request.files['audio']
    audio_data = audio_file.read()
    audio_filename = "recording_from_browser.wav"

    # --- 2. AmiVoice APIで音声認識と感情分析を実行 ---
    domain = {
        'grammarFileNames': '-a-general',
        'contentId': audio_filename,
        'sentimentAnalysis': 'True'
    }
    params = {
        'u': AMIVOICE_API_KEY,
        'd': ' '.join([f'{key}={urllib.parse.quote(value)}' for key, value in domain.items()]),
    }

    try:
        # ジョブのリクエストを送信
        request_response = requests.post(
            url=AMIVOICE_ENDPOINT,
            data=params,
            files={'a': (audio_filename, audio_data, audio_file.mimetype)}
        )
        request_response.raise_for_status()
        request_data = request_response.json()

        if 'sessionid' not in request_data:
            return jsonify({'error': 'AmiVoiceジョブの作成に失敗しました', 'details': request_data}), 500

        session_id = request_data['sessionid']

        # 結果が出るまでポーリング
        result = None
        # タイムアウトを180秒（18回 * 10秒）に延長
        for _ in range(18): 
            time.sleep(10)
            result_response = requests.get(
                url=f'{AMIVOICE_ENDPOINT}/{session_id}',
                headers={'Authorization': f'Bearer {AMIVOICE_API_KEY}'}
            )
            result_response.raise_for_status()
            result = result_response.json()
            if 'status' in result:
                if result['status'] == 'completed':
                    break # 成功したらループを抜ける
                if result['status'] == 'error':
                    # API側でエラーが起きた場合
                    return jsonify({'error': 'AmiVoiceで分析エラーが発生しました', 'details': result.get('message', '詳細不明')}), 500
        
        # ループが完了しても結果が'completed'でない場合はタイムアウトとみなす
        if not result or result.get('status') != 'completed':
             return jsonify({'error': 'AmiVoiceでの音声分析がタイムアウトしました。もう少し短い音声でお試しください。', 'details': result}), 500

        # --- 3. AmiVoiceの結果を解析 ---
        transcribed_text = result.get('text', '')
        # 最初のセグメントから感情を取得する (簡易的な方法)
        emotion = "ニュートラル" # デフォルト値
        if 'segments' in result and result['segments']:
            for segment in result['segments']:
                if 'sentiment' in segment and segment['sentiment']:
                    emotion = segment['sentiment'][0].get('label', 'ニュートラル')
                    break # 最初の感情が見つかったらループを抜ける
        
        # --- 4. Gemini APIでプロンプトと歌詞を生成 ---
        music_prompt = create_music_prompt(emotion, transcribed_text)
        lyrics = create_lyrics(emotion, transcribed_text)

        # --- 5. 結果をフロントエンドに返す ---
        return jsonify({
            'transcription': transcribed_text,
            'emotion': emotion,
            'music_prompt': music_prompt,
            'lyrics': lyrics
        })

    except requests.exceptions.HTTPError as e:
        # APIキーが不正な場合などはここに入ることが多い
        if e.response.status_code == 401:
             return jsonify({'error': 'AmiVoice APIキーが不正です。設定を確認してください。', 'details': str(e)}), 401
        return jsonify({'error': f'AmiVoice APIへのリクエストに失敗しました (HTTP {e.response.status_code})', 'details': str(e)}), 500
    except requests.exceptions.RequestException as e:
        return jsonify({'error': 'AmiVoice APIへの接続に失敗しました。ネットワーク接続を確認してください。', 'details': str(e)}), 500
    except Exception as e:
        return jsonify({'error': '予期せぬエラーが発生しました', 'details': str(e)}), 500

# --- サーバーを起動 ---
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

