import os
import io
import uuid
import logging
import base64
import json
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit # ★追加
from dotenv import load_dotenv
import requests
from pydub import AudioSegment
from janome.tokenizer import Tokenizer
import socketio # ★追加 (バックエンドからAmiVoiceへのWebSocketクライアント用)

# 環境変数をロード
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///music_generations.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'generated_music'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
socketio_app = SocketIO(app, cors_allowed_origins="*") # WebSocketを有効化, CORS許可

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Database Model
class MusicGeneration(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    original_text = db.Column(db.Text, nullable=False)
    detected_emotion = db.Column(db.String(50))
    generated_music_url = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<MusicGeneration {self.id}>'

with app.app_context():
    db.create_all()

# AmiVoice API Settings for Streaming Recognition
AMIVOICE_APP_KEY = os.getenv("AMIVOICE_APP_KEY")
AMIVOICE_PASSWORD = os.getenv("AMIVOICE_PASSWORD")
# AmiVoice Cloud Platform (ACP) のリアルタイム認識URL (これは一例です。実際のURLはドキュメントを確認してください)
# 通常は ws:// または wss:// で始まるWebSocket URLです
AMIVOICE_STREAMING_RECOGNITION_URL = "wss://acp-api.amivoice.com/v2/streaming_recognition"

TOPMEDIAAPI_KEY = os.getenv("TOPMEDIAAPI_API_KEY")

# --- 簡易感情分析関数 (変更なし) ---
def analyze_sentiment_japanese(text):
    t = Tokenizer()
    tokens = [str(token) for token in t.tokenize(text)]

    positive_keywords = ["楽しい", "嬉しい", "幸せ", "最高", "素晴らしい", "好き", "良い"]
    negative_keywords = ["悲しい", "寂しい", "辛い", "嫌い", "悪い", "怒り"]

    score = 0
    for keyword in positive_keywords:
        if keyword in text:
            score += 1
    for keyword in negative_keywords:
        if keyword in text:
            score -= 1

    if score > 0:
        return "positive"
    elif score < 0:
        return "negative"
    else:
        if "穏やか" in text or "落ち着く" in text:
            return "calm"
        return "neutral"

# --- HTTP ルート (変更なし) ---
@app.route('/')
def index():
    return send_from_directory('../frontend', 'index.html')

@app.route('/<path:filename>')
def serve_frontend_files(filename):
    return send_from_directory('../frontend', filename)

@app.route('/generated_music/<filename>')
def get_generated_music(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- WebSocket イベントハンドラ ---

# クライアントが接続した時
@socketio_app.on('connect')
def handle_connect():
    logging.info(f"Client connected: {request.sid}")
    # AmiVoiceへのWebSocket接続を確立
    # 各クライアント接続に対して新しいAmiVoice WebSocketクライアントを作成
    sio_amivoice = socketio.Client()

    @sio_amivoice.event
    def connect():
        logging.info("Connected to AmiVoice WebSocket.")
        # 接続時に認証情報を送信 (AmiVoiceのドキュメントに従う)
        # 例: AmiVoiceの認証メッセージ
        auth_message = {
            "command": "auth",
            "param": {
                "appKey": AMIVOICE_APP_KEY,
                "password": AMIVOICE_PASSWORD
            }
        }
        sio_amivoice.send(json.dumps(auth_message))
        # 認識パラメータを送信 (AmiVoiceのドキュメントに従う)
        # 例: 認識パラメータメッセージ
        param_message = {
            "command": "param",
            "param": {
                "grammarFileNames": "g-ja",
                "resultType": "json",
                "resultInterval": 500, # 部分認識結果の送信間隔 (ms)
                "segmentation": "true" # 発話区間検出を有効にする
            }
        }
        sio_amivoice.send(json.dumps(param_message))

        # 認識開始コマンドを送信
        sio_amivoice.send(json.dumps({"command": "start"}))
        logging.info("Sent start command to AmiVoice.")


    @sio_amivoice.event
    def disconnect():
        logging.info("Disconnected from AmiVoice WebSocket.")

    @sio_amivoice.event
    def message(data):
        # AmiVoiceからのメッセージを受信
        try:
            msg = json.loads(data)
            if msg.get("result"):
                # 認識結果を受信
                result = msg["result"]
                if result.get("text"):
                    transcribed_text = result["text"]
                    is_final = result.get("isFinal", False) # 最終認識結果かどうかのフラグ

                    logging.info(f"AmiVoice Result (Final: {is_final}): {transcribed_text}")
                    
                    # フロントエンドに部分認識結果を送信
                    emit('recognition_update', {'text': transcribed_text, 'is_final': is_final}, room=request.sid)

                    if is_final:
                        # 最終認識結果の場合、感情分析と音楽生成を行う
                        handle_final_recognition(transcribed_text, request.sid)
                        # AmiVoiceの認識を停止
                        sio_amivoice.send(json.dumps({"command": "stop"}))
                        sio_amivoice.disconnect() # AmiVoiceとの接続を切断
                        logging.info("Received final recognition from AmiVoice. Disconnecting AmiVoice client.")

            elif msg.get("error"):
                logging.error(f"AmiVoice Error: {msg['error']}")
                emit('recognition_error', {'message': f"音声認識エラー: {msg['error']}"}, room=request.sid)
                sio_amivoice.disconnect()

        except json.JSONDecodeError:
            logging.error(f"Invalid JSON from AmiVoice: {data}")
        except Exception as e:
            logging.error(f"Error processing AmiVoice message: {e}", exc_info=True)
            sio_amivoice.disconnect()

    @sio_amivoice.event
    def connect_error(data):
        logging.error(f"AmiVoice WebSocket connection failed: {data}")
        emit('recognition_error', {'message': f"AmiVoice接続エラー: {data}"}, room=request.sid)

    try:
        sio_amivoice.connect(AMIVOICE_STREAMING_RECOGNITION_URL, transports=['websocket'])
        # SocketIOのセッションにAmiVoiceクライアントを保存
        # これにより、このクライアントのセベートハンドラがこのセッションに紐付けられる
        request.sid_amivoice_client = sio_amivoice
    except Exception as e:
        logging.error(f"Failed to connect to AmiVoice: {e}")
        emit('recognition_error', {'message': f"AmiVoiceへの初期接続に失敗しました: {e}"}, room=request.sid)


# クライアントから音声データを受信した時
@socketio_app.on('audio_chunk')
def handle_audio_chunk(data):
    # AmiVoiceへのWebSocketクライアントが接続されていることを確認
    sio_amivoice = getattr(request, 'sid_amivoice_client', None)
    if sio_amivoice and sio_amivoice.connected:
        try:
            # フロントエンドから受け取った音声チャンクをそのままAmiVoiceに送信
            # AmiVoiceのリアルタイム認識は、生データまたは特定のエンコード形式を期待します
            # フロントエンドから送られてくるWebMデータをそのまま送るか、
            # 必要に応じてサーバー側で変換してから送ります。
            # ここでは、フロントエンドで適切なフォーマットでエンコードされていると仮定し、そのまま送信。
            # AmiVoiceがWAV形式を期待する場合、フロントエンドでWAVにエンコードするか、
            # ここでpydubを使って変換してから送る必要があります。
            # 例: pydubで変換する場合 (オーバーヘッドが増えるため推奨はフロントエンドでの対応)
            # audio_segment = AudioSegment.from_file(io.BytesIO(data), format="webm")
            # wav_buffer = io.BytesIO()
            # audio_segment.export(wav_buffer, format="wav")
            # sio_amivoice.send(wav_buffer.getvalue())
            sio_amivoice.send(data)
        except Exception as e:
            logging.error(f"Error sending audio chunk to AmiVoice: {e}", exc_info=True)
            emit('recognition_error', {'message': f"音声データ送信エラー: {e}"}, room=request.sid)
    else:
        logging.warning("AmiVoice client not connected for SID: %s", request.sid)
        emit('recognition_error', {'message': '音声認識サービスが利用できません。'}, room=request.sid)

# クライアントが録音を停止した時
@socketio_app.on('stop_recording')
def handle_stop_recording():
    logging.info(f"Stop recording signal received from client: {request.sid}")
    sio_amivoice = getattr(request, 'sid_amivoice_client', None)
    if sio_amivoice and sio_amivoice.connected:
        # AmiVoiceに認識終了コマンドを送信
        sio_amivoice.send(json.dumps({"command": "stop"}))
        logging.info("Sent stop command to AmiVoice.")
    else:
        logging.warning("AmiVoice client not connected when stop_recording received.")

# クライアントが切断した時
@socketio_app.on('disconnect')
def handle_disconnect():
    logging.info(f"Client disconnected: {request.sid}")
    sio_amivoice = getattr(request, 'sid_amivoice_client', None)
    if sio_amivoice and sio_amivoice.connected:
        sio_amivoice.disconnect() # AmiVoiceとの接続も切断
        logging.info("Disconnected AmiVoice client on client disconnect.")

# 最終認識結果を受け取った後の処理 (音楽生成)
def handle_final_recognition(transcribed_text, client_sid):
    try:
        # 感情分析
        detected_emotion = analyze_sentiment_japanese(transcribed_text)
        logging.info(f"Detected emotion: {detected_emotion}")

        # TopMediaAPI用のプロンプト変換ロジック (感情を組み込む)
        music_prompt = f"Generate instrumental music based on this description: '{transcribed_text}'. "
        if detected_emotion == "positive":
            music_prompt += "It should be joyful, uplifting, and lively, suitable for a happy cultural festival."
        elif detected_emotion == "negative":
            music_prompt += "It should evoke a contemplative or slightly melancholic mood, but with a hopeful undertone, suitable for a reflective cultural festival moment."
        elif detected_emotion == "calm":
            music_prompt += "It should be peaceful, serene, and calming, suitable for a relaxing cultural festival atmosphere."
        else:
            music_prompt += "Make it suitable for a cultural festival with a general positive and energetic vibe."

        logging.info(f"Music generation prompt: '{music_prompt}'")

        # TopMediaAPIで音楽を生成
        topmediaapi_url = "YOUR_TOPMEDIAAPI_MUSIC_GENERATION_ENDPOINT"
        headers = {
            "Authorization": f"Bearer {TOPMEDIAAPI_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "text_prompt": music_prompt,
            "duration": 30,
            "output_format": "mp3"
        }

        topmediaapi_response = requests.post(topmediaapi_url, headers=headers, json=payload)
        topmediaapi_response.raise_for_status()
        music_data = topmediaapi_response.json()

        generated_music_url = None
        if "music_file_url" in music_data:
            generated_music_url = music_data["music_file_url"]
        elif "audio_data_base64" in music_data:
            base64_audio = music_data["audio_data_base64"]
            audio_bytes = base64.b64decode(base64_audio)

            file_extension = payload.get("output_format", "mp3")
            filename = f"{uuid.uuid4()}.{file_extension}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            with open(filepath, 'wb') as f:
                f.write(audio_bytes)

            generated_music_url = url_for('get_generated_music', filename=filename, _external=True)
        else:
            logging.error("TopMediaAPI response missing music_file_url or audio_data_base64.")
            emit('music_generation_error', {'message': '音楽生成APIからの応答形式が不正です。'}, room=client_sid)
            return

        if not generated_music_url:
            logging.error("Failed to get music file URL.")
            emit('music_generation_error', {'message': '音楽ファイルのURLを取得できませんでした。'}, room=client_sid)
            return

        # データベースにメタデータを保存
        with app.app_context(): # データベース操作はアプリケーションコンテキスト内で行う
            new_generation = MusicGeneration(
                original_text=transcribed_text,
                detected_emotion=detected_emotion,
                generated_music_url=generated_music_url
            )
            db.session.add(new_generation)
            db.session.commit()
            logging.info(f"Music generation record saved: {new_generation.id}")

        # フロントエンドに音楽生成結果を送信
        emit('music_generated', {
            "success": True,
            "original_text": transcribed_text,
            "detected_emotion": detected_emotion,
            "music_url": generated_music_url
        }, room=client_sid)

    except requests.exceptions.RequestException as e:
        logging.error(f"API request error during music generation: {e}")
        emit('music_generation_error', {'message': f"音楽生成APIとの通信エラー: {e}"}, room=client_sid)
    except Exception as e:
        logging.error(f"Unexpected error during music generation: {e}", exc_info=True)
        emit('music_generation_error', {'message': f"内部サーバーエラー: {e}"}, room=client_sid)


if __name__ == '__main__':
    # Flask-SocketIO を使う場合、app.run() ではなく socketio_app.run() を使う
    socketio_app.run(app, debug=True, host='0.0.0.0', port=5000)