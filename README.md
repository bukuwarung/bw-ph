# <b>Payment Transaction Voice API Documentation</b>

**Version:** 1.0  
**Base URL:** `http://localhost:8765`  
**WebSocket URL:** `ws://localhost:8765`

## <b>Overview</b>

Sekar is a voice-powered assistant for BukuWarung EDC merchants that provides real-time transaction history queries using Gemini Live API. It supports both REST API endpoints and WebSocket connections for real-time voice conversations.

---

## Table of Contents

1. [REST API Endpoints](#rest-api-endpoints)
   - [Health Check](#1-health-check)
   - [Query Transactions](#2-query-transactions)
   - [Parse Date Reference](#3-parse-date-reference)
2. [WebSocket API](#websocket-api)
   - [Connection](#websocket-connection)
   - [Client Messages](#client-to-server-messages)
   - [Server Messages](#server-to-client-messages)

---

## REST API Endpoints

### 1. Health Check

Check the API server status and configuration.

**Endpoint:** `GET /api/health`

**Request:**
```http
GET /api/health HTTP/1.1
Host: localhost:8765
```

**Response:**
```json
{
  "status": "healthy",
  "mode": "gemini-live real-time voice",
  "model": "gemini-2.5-flash-native-audio-preview-09-2025",
  "port": 8765,
  "timestamp": "2025-12-15T14:30:00+07:00"
}
```

**Status Codes:**
- `200 OK` - Server is healthy

---

### 2. Query Transactions

Query EDC transaction history with natural language time references.

**Endpoint:** `POST /api/transactions/query`

**Request Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "query_type": "transfer",
  "time_period": "today",
  "status_filter": "SUCCESS",
  "page_number": 0,
  "page_size": 100
}
```

**Request Parameters:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query_type` | string | Yes | Type of query: `"transfer"` or `"balance"` |
| `time_period` | string | Yes | Natural language time reference (e.g., "today", "yesterday", "last week", "3 days ago") |
| `status_filter` | string | No | Filter by status: `"SUCCESS"`, `"PENDING"`, or `"FAILED"` |
| `page_number` | integer | No | Page number for pagination (default: 0) |
| `page_size` | integer | No | Items per page (default: 100, max: 500) |

**Response (Success):**
```json
{
  "success": true,
  "transaction_type": "TRANSFER_POSTING",
  "time_period": "today",
  "start_date": "2025-12-15T00:00:00+07:00",
  "end_date": "2025-12-15T23:59:59+07:00",
  "total_count": 45,
  "filtered_count": 42,
  "transactions": [
    {
      "id": "TXN123456",
      "status": "SUCCESS",
      "total_amount": 500000,
      "provider": "BCA",
      "created_at": "2025-12-15T10:30:00+07:00",
      "recipient_name": "John Doe",
      "recipient_account": "1234567890"
    }
  ],
  "summary": {
    "total_transactions": 42,
    "time_period": "today",
    "transaction_type": "TRANSFER_POSTING",
    "status_breakdown": {
      "SUCCESS": 40,
      "PENDING": 2
    },
    "total_amount": 21000000,
    "total_amount_formatted": "Rp 21,000,000",
    "successful_transfers": 40,
    "successful_amount": 20500000,
    "successful_amount_formatted": "Rp 20,500,000",
    "average_amount_formatted": "Rp 512,500",
    "provider_breakdown": {
      "BCA": 25,
      "MANDIRI": 10,
      "BNI": 7
    }
  }
}
```

**Response (Error):**
```json
{
  "success": false,
  "error": "Failed to connect to BukuWarung API",
  "total_count": 0,
  "transactions": [],
  "summary": {}
}
```

**Status Codes:**
- `200 OK` - Request processed (check `success` field)
- `422 Unprocessable Entity` - Invalid request parameters

**Example Requests:**

1. **Today's transfers:**
```bash
curl -X POST http://localhost:8765/api/transactions/query \
  -H "Content-Type: application/json" \
  -d '{
    "query_type": "transfer",
    "time_period": "today"
  }'
```

2. **Last week's balance checks:**
```bash
curl -X POST http://localhost:8765/api/transactions/query \
  -H "Content-Type: application/json" \
  -d '{
    "query_type": "balance",
    "time_period": "last week"
  }'
```

3. **Failed transactions in the last 30 days:**
```bash
curl -X POST http://localhost:8765/api/transactions/query \
  -H "Content-Type: application/json" \
  -d '{
    "query_type": "transfer",
    "time_period": "30 days ago",
    "status_filter": "FAILED"
  }'
```

---

### 3. Parse Date Reference

Parse natural language time references into structured date ranges using Gemini AI.

**Endpoint:** `GET /api/transactions/parse-date`

**Request:**
```http
GET /api/transactions/parse-date?time_period=last%20week HTTP/1.1
Host: localhost:8765
```

**Query Parameters:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `time_period` | string | No | Natural language time reference (default: "today") |

**Response:**
```json
{
  "time_period": "last week",
  "start_date": "2025-12-08T00:00:00+07:00",
  "end_date": "2025-12-14T23:59:59+07:00",
  "start_date_api_format": "2025-12-08T00:00:00+07:00",
  "end_date_api_format": "2025-12-14T23:59:59+07:00"
}
```

**Supported Time References:**
- `"today"`, `"yesterday"`
- `"this week"`, `"last week"`, `"2 weeks ago"`
- `"this month"`, `"last month"`, `"3 months ago"`
- `"7 days ago"`, `"30 days ago"`
- `"kemarin"`, `"minggu ini"`, `"bulan lalu"` (Indonesian)

**Status Codes:**
- `200 OK` - Successfully parsed

**Example:**
```bash
curl "http://localhost:8765/api/transactions/parse-date?time_period=kemarin"
```

---

## WebSocket API

### WebSocket Connection

Connect to the real-time voice conversation endpoint.

**URL:** `ws://localhost:8765`

**Protocol:** WebSocket (RFC 6455)

**Audio Format:**
- **Encoding:** PCM (Linear 16-bit)
- **Sample Rate:** 16,000 Hz
- **Channels:** Mono
- **Byte Order:** Little-endian

### Connection Flow

```
1. Client → Connect WebSocket
2. Server → Send "connected" message
3. Client ⇄ Server → Bidirectional audio/text streaming
4. Server → Send responses (audio + transcripts)
5. Client/Server → Close connection
```

---

### Client to Server Messages

#### 1. Audio Data (Binary)

Send raw PCM audio data as binary WebSocket frames.

**Format:** Binary WebSocket frame

**Data:** Raw PCM audio bytes (16-bit, 16kHz, mono)

**Example (JavaScript):**
```javascript
// Send audio chunk
websocket.send(audioBuffer); // ArrayBuffer or Uint8Array
```

---

#### 2. Text Input

Send text message instead of voice.

**Format:** JSON

**Payload:**
```json
{
  "type": "text",
  "text": "Berapa transfer hari ini?"
}
```

**Example:**
```javascript
websocket.send(JSON.stringify({
  type: "text",
  text: "Show me today's transactions"
}));
```

---

#### 3. End Turn Signal

Signal end of user's speaking turn.

**Payload:**
```json
{
  "type": "end_turn"
}
```

or

```json
{
  "type": "commit"
}
```

---

#### 4. Interrupt Signal

Interrupt Gemini's current response.

**Payload:**
```json
{
  "type": "interrupt"
}
```

**Use case:** Stop AI mid-sentence when user starts speaking

---

#### 5. Ping (Keep-alive)

**Payload:**
```json
{
  "type": "ping"
}
```

**Response:**
```json
{
  "type": "pong"
}
```

---

### Server to Client Messages

All server messages are JSON strings sent via WebSocket text frames.

#### 1. Connection Established

Sent immediately after successful WebSocket connection.

**Payload:**
```json
{
  "type": "connected",
  "message": "Connected to Gemini Live - Start speaking!"
}
```

---

#### 2. Audio Response

AI voice response audio data.

**Payload:**
```json
{
  "type": "audio",
  "data": "BASE64_ENCODED_PCM_AUDIO_DATA"
}
```

**Audio Format:** Base64-encoded PCM audio (16-bit, 16kHz, mono)

**Example (JavaScript):**
```javascript
if (message.type === "audio") {
  const audioData = base64ToArrayBuffer(message.data);
  playAudio(audioData);
}
```

---

#### 3. Transcript

Text transcript of AI's response.

**Payload:**
```json
{
  "type": "transcript",
  "text": "Hari ini ada 42 transaksi transfer dengan total Rp 20.500.000"
}
```

**Use case:** Display what AI is saying for accessibility

---

#### 4. Turn Complete

AI has finished speaking.

**Payload:**
```json
{
  "type": "turn_complete",
  "turn": 3
}
```

**Use case:** Stop audio playback, allow user to speak

---

#### 5. Function Call

AI is calling a function to fetch data.

**Payload:**
```json
{
  "type": "function_call",
  "function": "query_transaction_history",
  "args": {
    "query_type": "transfer",
    "time_period": "hari ini"
  }
}
```

**Use case:** Show loading indicator

---

#### 6. Transaction Result

Transaction query results.

**Payload:**
```json
{
  "type": "transaction_result",
  "summary": {
    "total_transactions": 42,
    "total_amount_formatted": "Rp 20,500,000",
    "successful_transfers": 40,
    "status_breakdown": {
      "SUCCESS": 40,
      "PENDING": 2
    }
  },
  "time_period": "hari ini",
  "total_count": 42
}
```

**Use case:** Display transaction summary in UI

---

#### 7. Interrupted

AI response was interrupted.

**Payload:**
```json
{
  "type": "interrupted"
}
```

**Use case:** User spoke while AI was talking

---

#### 8. Error

Error occurred during processing.

**Payload:**
```json
{
  "type": "error",
  "message": "Gemini API client not initialized. Check GEMINI_API_KEY."
}
```

---

## Complete WebSocket Example

### JavaScript Client

```javascript
const ws = new WebSocket('ws://localhost:8765');

// Connection opened
ws.onopen = () => {
  console.log('Connected to Sekar Voice Assistant');
};

// Receive messages
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  
  switch (message.type) {
    case 'connected':
      console.log('Ready to speak!');
      break;
      
    case 'audio':
      // Decode and play audio
      const audioData = base64ToArrayBuffer(message.data);
      playAudioChunk(audioData);
      break;
      
    case 'transcript':
      console.log('AI said:', message.text);
      displayTranscript(message.text);
      break;
      
    case 'turn_complete':
      console.log('AI finished speaking');
      enableMicrophone();
      break;
      
    case 'function_call':
      console.log('Fetching data:', message.function);
      showLoadingIndicator();
      break;
      
    case 'transaction_result':
      console.log('Results:', message.summary);
      displayTransactionSummary(message.summary);
      break;
      
    case 'interrupted':
      console.log('AI was interrupted');
      stopAudioPlayback();
      break;
      
    case 'error':
      console.error('Error:', message.message);
      break;
  }
};

// Send audio from microphone
function streamAudio(audioBuffer) {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(audioBuffer); // Send raw PCM bytes
  }
}

// Send text message
function sendText(text) {
  ws.send(JSON.stringify({
    type: 'text',
    text: text
  }));
}

// Signal end of turn
function endTurn() {
  ws.send(JSON.stringify({ type: 'end_turn' }));
}

// Interrupt AI
function interrupt() {
  ws.send(JSON.stringify({ type: 'interrupt' }));
}
```

---

## Natural Language Examples

The AI assistant understands Indonesian and English commands:

**Indonesian:**
- "Berapa transfer hari ini?"
- "Lihat transaksi kemarin"
- "Ada berapa cek saldo minggu ini?"
- "Total transfer bulan lalu berapa?"
- "Tampilkan transaksi gagal 7 hari terakhir"

**English:**
- "How many transfers today?"
- "Show me yesterday's transactions"
- "How many balance checks this week?"
- "What's the total transfers last month?"
- "Show failed transactions in the last 7 days"

---

## Environment Variables

Required configuration:

```env
GEMINI_API_KEY=your_gemini_api_key
BUKUWARUNG_API_BASE=https://api-dev.bukuwarung.com/golden-gate/api/edc
BUKUWARUNG_TOKEN=your_bearer_token
BUKUWARUNG_SESSION=your_jsessionid
```

---

## Error Handling

### REST API Errors

All REST endpoints return JSON responses. Check the `success` field:

```json
{
  "success": false,
  "error": "Error description here"
}
```

### WebSocket Errors

Errors are sent as JSON messages:

```json
{
  "type": "error",
  "message": "Error description"
}
```

**Common Error Scenarios:**
- API key not configured
- BukuWarung API unreachable
- Invalid date format
- Connection timeout
- Audio format mismatch

---

## Rate Limits

- **WebSocket:** 1 connection per client
- **REST API:** No explicit rate limit (depends on BukuWarung API)
- **Transaction Query:** Max 500 results per page

---

## Notes

1. **Time Zone:** All timestamps are in Asia/Jakarta (WIB, UTC+7)
2. **Audio Format:** Must be PCM 16-bit, 16kHz, mono
3. **Function Calls:** Processed automatically, results returned via WebSocket
4. **Multi-turn Conversations:** WebSocket maintains context across turns
5. **Connection Timeout:** WebSocket ping interval is 30 seconds

---

## Support

For issues or questions, check application logs for detailed error messages.