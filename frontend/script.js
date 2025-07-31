document.addEventListener('DOMContentLoaded', () => {
    const startButton = document.getElementById('startButton');
    const stopButton = document.getElementById('stopButton');
    const recordingStatus = document.getElementById('recordingStatus');
    const realtimeRecognitionDiv = document.getElementById('realtimeRecognition'); // 新しい要素
    const partialTextSpan = document.getElementById('partialText'); // 新しい要素
    const resultDiv = document.getElementById('result');
    const originalTextSpan = document.getElementById('originalText');
    const detectedEmotionSpan = document.getElementById('detectedEmotion');
    const musicPlayer = document.getElementById('musicPlayer');
    const downloadLink = document.getElementById('downloadLink');
    const errorMessageDiv = document.getElementById('errorMessage');
    const loadingIndicator = document.getElementById('loadingIndicator');

    let mediaRecorder;
    let socket; // Socket.IO クライアントインスタンス
    let stream; // MediaStreamを保持

    // Socket.IO サーバーに接続
    // デプロイ時は 'http://your-server-ip-or-domain:port' に変更
    socket = io(); 

    // バックエンドからの認識結果更新イベント
    socket.on('recognition_update', (data) => {
        partialTextSpan.textContent = data.text;
        realtimeRecognitionDiv.style.display = 'block';
        if (data.is_final) {
            recordingStatus.textContent = '最終認識結果を受信しました。音楽生成中...';
            realtimeRecognitionDiv.style.display = 'none'; // 最終結果が出たら部分認識は非表示
            loadingIndicator.style.display = 'flex'; // 音楽生成中のローディング表示
        }
    });

    // バックエンドからの音楽生成完了イベント
    socket.on('music_generated', (data) => {
        loadingIndicator.style.display = 'none'; // ローディング非表示
        if (data.success) {
            originalTextSpan.textContent = data.original_text;
            detectedEmotionSpan.textContent = data.detected_emotion;
            musicPlayer.src = data.music_url;
            downloadLink.href = data.music_url;
            downloadLink.style.display = 'inline-block';
            resultDiv.style.display = 'block';
            musicPlayer.load();
            musicPlayer.play();
            recordingStatus.textContent = '音楽が正常に生成されました！';
        } else {
            errorMessageDiv.textContent = data.error || '音楽生成中に不明なエラーが発生しました。';
            errorMessageDiv.style.display = 'block';
        }
    });

    // バックエンドからのエラーイベント
    socket.on('recognition_error', (data) => {
        loadingIndicator.style.display = 'none';
        errorMessageDiv.textContent = data.message || '音声認識中にエラーが発生しました。';
        errorMessageDiv.style.display = 'block';
        startButton.disabled = false;
        stopButton.disabled = true;
        recordingStatus.textContent = '';
        if (stream) {
            stream.getTracks().forEach(track => track.stop());
        }
    });

    socket.on('music_generation_error', (data) => {
        loadingIndicator.style.display = 'none';
        errorMessageDiv.textContent = data.message || '音楽生成中にエラーが発生しました。';
        errorMessageDiv.style.display = 'block';
        startButton.disabled = false;
        stopButton.disabled = true;
        recordingStatus.textContent = '';
    });


    startButton.addEventListener('click', async () => {
        // 各種表示をリセット
        resultDiv.style.display = 'none';
        errorMessageDiv.style.display = 'none';
        loadingIndicator.style.display = 'none';
        realtimeRecognitionDiv.style.display = 'none';
        partialTextSpan.textContent = '';
        recordingStatus.textContent = 'マイクアクセスを要求中...';

        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            
            // MediaRecorderを初期化し、音声データをチャンクで取得
            // AmiVoiceが期待するフォーマットに合わせてmimeTypeとaudioBitsPerSecondを設定することが重要です
            // 例: 16kHz, 16bit, mono WAV を想定する場合
            // ただし、ブラウザのMediaRecorderがWAVを直接サポートしない場合が多いので、
            // audio/webm を使い、バックエンドで変換するか、AmiVoiceがwebmをサポートするか確認が必要です。
            // ここでは、デフォルトの audio/webm で進めます。
            mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });

            // 音声データが利用可能になるたびにWebSocketで送信
            mediaRecorder.ondataavailable = event => {
                if (event.data.size > 0) {
                    socket.emit('audio_chunk', event.data); // 音声データを直接送信
                }
            };

            // 録音が停止した時の処理 (リアルタイム認識では通常、stop_recordingイベントで終了)
            mediaRecorder.onstop = () => {
                // ここでは特に何もしない。stop_recordingイベントがサーバーに送られる
                // マイクのトラックを停止し、マイクが使用中であることを解除
                stream.getTracks().forEach(track => track.stop());
            };

            // 録音開始
            mediaRecorder.start(250); // 250msごとにデータチャンクを生成
            recordingStatus.textContent = '録音中...はっきりと話してください！';
            startButton.disabled = true;
            stopButton.disabled = false;

        } catch (err) {
            console.error('マイクアクセスエラー:', err);
            recordingStatus.textContent = '';
            errorMessageDiv.textContent = 'マイクにアクセスできませんでした。マイクの使用を許可して、もう一度お試しください。';
            errorMessageDiv.style.display = 'block';
            startButton.disabled = false;
            stopButton.disabled = true;
        }
    });

    stopButton.addEventListener('click', () => {
        if (mediaRecorder && mediaRecorder.state === 'recording') {
            mediaRecorder.stop(); // MediaRecorderを停止
            socket.emit('stop_recording'); // サーバーに録音停止シグナルを送信
            recordingStatus.textContent = '録音停止。最終認識結果を待機中...';
            startButton.disabled = true;
            stopButton.disabled = true;
        }
    });
});