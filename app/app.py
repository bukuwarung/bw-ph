#!/usr/bin/env python3
"""
BukuWarung Transaction History - FastAPI + Gemini Live Voice Integration
IMPROVED VERSION with better real-time conversation handling
Port: 7070
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import logging
from dotenv import load_dotenv
import requests
import asyncio
import json
import base64
import websockets

# Import Gemini client library
from google import genai
from google.genai import types

load_dotenv()

# --- Configuration ---
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logging.getLogger("websockets.server").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BUKUWARUNG_API_BASE = os.getenv("BUKUWARUNG_API_BASE", "https://api-dev.bukuwarung.com/golden-gate/api/edc")
BUKUWARUNG_TOKEN = os.getenv("BUKUWARUNG_TOKEN", "")
BUKUWARUNG_SESSION = os.getenv("BUKUWARUNG_SESSION", "")
JAKARTA_TZ = ZoneInfo("Asia/Jakarta")

# Server Configuration
SINGLE_PORT = 7070
HOST = "0.0.0.0"

# Initialize Gemini client
client = None
try:
    if GEMINI_API_KEY:
        client = genai.Client(api_key=GEMINI_API_KEY)
    else:
        logger.error("GEMINI_API_KEY not found in environment.")
except Exception as e:
    logger.error(f"Failed to initialize Gemini Client: {e}")

# --- MODEL DEFINITIONS ---
LIVE_MODEL = "gemini-2.5-flash-native-audio-preview-09-2025"  # Voice streaming
MODEL_ID = "gemini-2.0-flash"  # For date parsing


# =============================================================================
# TRANSACTION & DATE PARSING FUNCTIONS
# =============================================================================

PARSE_DATE_RANGE_FUNCTION = {
    "name": "parse_date_range",
    "description": "Parse natural language time references into structured date range parameters",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "period": {
                "type": "STRING",
                "enum": ["today", "yesterday", "day", "week", "month", "year", "days", "weeks", "months"],
                "description": "Time period unit"
            },
            "offset_value": {
                "type": "INTEGER",
                "description": "Number of units to offset",
                "default": 0
            },
            "relative_to": {
                "type": "STRING",
                "enum": ["current", "past", "future"],
                "description": "Relative time position"
            },
            "include_current": {
                "type": "BOOLEAN",
                "default": True
            }
        },
        "required": ["period", "relative_to"]
    }
}


def calculate_date_range(period: str, relative_to: str, offset_value: int = 0, 
                         include_current: bool = True) -> Tuple[datetime, datetime]:
    """Calculate actual date range from parsed parameters"""
    now = datetime.now(JAKARTA_TZ)
    
    def start_of_day(dt): 
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    
    def end_of_day(dt): 
        return dt.replace(hour=23, minute=59, second=59, microsecond=0)
    
    if period == "today":
        return start_of_day(now), end_of_day(now)
    
    elif period == "yesterday":
        yesterday = now - timedelta(days=1)
        return start_of_day(yesterday), end_of_day(yesterday)
    
    elif period == "days":
        if relative_to == "past":
            start = start_of_day(now - timedelta(days=offset_value))
            end = end_of_day(now) if include_current else end_of_day(now - timedelta(days=1))
        else:
            start = start_of_day(now + timedelta(days=1))
            end = end_of_day(now + timedelta(days=offset_value))
        return start, end
    
    elif period == "week":
        if relative_to == "current":
            start = start_of_day(now - timedelta(days=now.weekday()))
            end = end_of_day(now)
        elif relative_to == "past":
            offset_value = offset_value or 1
            target_week = now - timedelta(days=now.weekday()) - timedelta(weeks=offset_value)
            start = start_of_day(target_week)
            end = end_of_day(target_week + timedelta(days=6))
        else:
            target_week = now - timedelta(days=now.weekday()) + timedelta(weeks=offset_value)
            start = start_of_day(target_week)
            end = end_of_day(target_week + timedelta(days=6))
        return start, end
    
    elif period == "month":
        if relative_to == "current":
            start = start_of_day(now.replace(day=1))
            end = end_of_day(now)
        elif relative_to == "past":
            offset_value = offset_value or 1
            first_day = now.replace(day=1)
            target_month = first_day
            for _ in range(offset_value):
                target_month = (target_month - timedelta(days=1)).replace(day=1)
            start = start_of_day(target_month)
            next_month = (target_month.replace(day=28) + timedelta(days=4)).replace(day=1)
            last_day = next_month - timedelta(days=1)
            end = end_of_day(last_day)
        else:
            first_day = now.replace(day=1)
            target_month = first_day
            for _ in range(offset_value):
                target_month = (target_month.replace(day=28) + timedelta(days=4)).replace(day=1)
            start = start_of_day(target_month)
            next_month = (target_month.replace(day=28) + timedelta(days=4)).replace(day=1)
            last_day = next_month - timedelta(days=1)
            end = end_of_day(last_day)
        return start, end
    
    return start_of_day(now), end_of_day(now)


def parse_time_reference_with_gemini(time_phrase: str) -> Tuple[datetime, datetime]:
    """Parse time reference using Gemini AI"""
    now = datetime.now(JAKARTA_TZ)
    
    try:
        tools = types.Tool(function_declarations=[PARSE_DATE_RANGE_FUNCTION])
        config = types.GenerateContentConfig(
            tools=[tools],
            temperature=0.1
        )
        
        prompt = f"""Today is {now.strftime('%A, %Y-%m-%d')}.
Parse this time reference: "{time_phrase}"
Return the parameters for calculating the date range."""
        
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=prompt,
            config=config
        )
        
        if response.candidates[0].content.parts[0].function_call:
            function_call = response.candidates[0].content.parts[0].function_call
            params = dict(function_call.args)
            
            start_date, end_date = calculate_date_range(
                period=params.get("period", "today"),
                relative_to=params.get("relative_to", "current"),
                offset_value=params.get("offset_value", 0),
                include_current=params.get("include_current", True)
            )
            
            logger.info(f"✅ Parsed '{time_phrase}' → {start_date.date()} to {end_date.date()}")
            return start_date, end_date
        
        logger.warning(f"No function call from Gemini for '{time_phrase}'")
        return calculate_date_range("today", "current")
    
    except Exception as e:
        logger.error(f"Gemini parsing error: {e}")
        return calculate_date_range("today", "current")


def format_datetime_for_api(dt: datetime) -> str:
    """Format datetime for BukuWarung API"""
    dt_no_micro = dt.replace(microsecond=0)
    return dt_no_micro.strftime("%Y-%m-%dT%H:%M:%S+07:00")


def summarize_transactions(transactions: List[dict], transaction_type: str, time_period: str) -> dict:
    """Summarize transaction data"""
    if not transactions:
        return {"total_transactions": 0, "message": f"No transactions found"}
    
    summary = {
        "total_transactions": len(transactions),
        "time_period": time_period,
        "transaction_type": transaction_type
    }
    
    status_counts = {}
    for txn in transactions:
        status = txn.get("status", "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1
    summary["status_breakdown"] = status_counts
    
    if transaction_type == "TRANSFER_POSTING":
        total_amount = sum(txn.get("total_amount", 0) for txn in transactions)
        successful_txns = [t for t in transactions if t.get("status") == "SUCCESS"]
        successful_amount = sum(txn.get("total_amount", 0) for txn in successful_txns)
        
        summary["total_amount"] = total_amount
        summary["total_amount_formatted"] = f"Rp {total_amount:,}"
        summary["successful_transfers"] = len(successful_txns)
        summary["successful_amount"] = successful_amount
        summary["successful_amount_formatted"] = f"Rp {successful_amount:,}"
        
        if successful_txns:
            avg = successful_amount / len(successful_txns)
            summary["average_amount_formatted"] = f"Rp {avg:,.0f}"
    
    elif transaction_type == "BALANCE_INQUIRY":
        summary["total_balance_checks"] = len(transactions)
        summary["successful_checks"] = len([t for t in transactions if t.get("status") == "SUCCESS"])
    
    provider_counts = {}
    for txn in transactions:
        provider = txn.get("provider", "UNKNOWN")
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
    summary["provider_breakdown"] = provider_counts
    
    return summary


def query_transaction_history(
    query_type: str = "transfer",
    time_period: str = "today",
    status_filter: Optional[str] = None,
    page_number: int = 0,
    page_size: int = 100
) -> dict:
    """Query transaction history with AI-powered date parsing"""
    try:
        transaction_type = "TRANSFER_POSTING" if query_type.lower() == "transfer" else "BALANCE_INQUIRY"
        
        start_date, end_date = parse_time_reference_with_gemini(time_period)
        
        url = f"{BUKUWARUNG_API_BASE}/transaction-history/filter"
        params = {
            "start_date": format_datetime_for_api(start_date),
            "end_date": format_datetime_for_api(end_date),
            "type": transaction_type,
            "page_number": page_number,
            "page_size": min(page_size, 500)
        }
        
        headers = {
            "Authorization": f"Bearer {BUKUWARUNG_TOKEN}",
            "Cookie": f"JSESSIONID={BUKUWARUNG_SESSION}"
        }
        
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        transactions = data.get("history", [])
        
        if status_filter:
            transactions = [t for t in transactions if t.get("status", "").upper() == status_filter.upper()]
        
        summary = summarize_transactions(transactions, transaction_type, time_period)
        
        return {
            "success": True,
            "transaction_type": transaction_type,
            "time_period": time_period,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "total_count": data.get("total_count", 0),
            "filtered_count": len(transactions),
            "transactions": transactions,
            "summary": summary
        }
        
    except Exception as e:
        logger.error(f"Query error: {e}")
        return {
            "success": False,
            "error": str(e),
            "total_count": 0,
            "transactions": [],
            "summary": {}
        }


# Function declarations for Gemini Live
TRANSACTION_FUNCTIONS = [
    {
        "name": "query_transaction_history",
        "description": """Query EDC transaction history untuk melihat riwayat transaksi transfer atau cek saldo.

Gunakan fungsi ini ketika user bertanya tentang:
- Riwayat transfer atau transaksi ("berapa transfer hari ini", "lihat transaksi kemarin")
- Cek saldo ("ada berapa cek saldo minggu ini")
- Total transaksi dalam periode tertentu
- Ringkasan penjualan atau aktivitas EDC""",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query_type": {
                    "type": "STRING",
                    "enum": ["transfer", "balance"],
                    "description": "'transfer' untuk riwayat transfer uang, 'balance' untuk riwayat cek saldo"
                },
                "time_period": {
                    "type": "STRING",
                    "description": "Waktu dalam bahasa natural: 'hari ini', 'kemarin', 'minggu ini', 'bulan lalu', '7 hari terakhir', dll"
                },
                "status_filter": {
                    "type": "STRING",
                    "enum": ["SUCCESS", "PENDING", "FAILED"],
                    "description": "Optional: filter berdasarkan status transaksi"
                }
            },
            "required": ["query_type", "time_period"]
        }
    }
]


# =============================================================================
# GEMINI LIVE WEBSOCKET HANDLER (IMPROVED)
# =============================================================================

class ClientWebSocketHandler:
    """WebSocket server for real-time voice conversation - IMPROVED VERSION"""
    
    def __init__(self):
        self.gemini_session = None
        self.client_ws = None
        self.is_active = False
        self.turn_count = 0
    
    async def handle_client(self, websocket):
        """Handle incoming WebSocket connection from web client"""
        
        logger.info(f"👤 Voice client connected: {websocket.remote_address}")
        self.client_ws = websocket
        self.is_active = True
        self.turn_count = 0
        
        try:
            if not client:
                await websocket.send(json.dumps({
                    "type": "error",
                    "message": "Gemini API client not initialized. Check GEMINI_API_KEY."
                }))
                return

            tools = [{"function_declarations": TRANSACTION_FUNCTIONS}]
            
            # Speech config for natural voice
            speech_config_obj = types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Vindemiatrix"
                    )
                )
            )

            config = {
                "response_modalities": ["AUDIO"],
                "system_instruction": """Kamu adalah Sekar, asisten suara yang ramah dan helpful untuk merchant EDC BukuWarung di Indonesia.

KEPRIBADIAN:
- Ramah, hangat, dan sabar seperti teman yang membantu
- Gunakan bahasa Indonesia yang natural dan santai (boleh pakai "kak", "ya", "nih", dll)
- Jawab dengan singkat dan jelas, cocok untuk percakapan suara

KEMAMPUAN:
1. SAPAAN & OBROLAN RINGAN: Tanggapi sapaan dengan ramah. Bisa ngobrol ringan.

2. RIWAYAT TRANSAKSI: Ketika user bertanya tentang:
   - Transfer atau transaksi ("berapa transfer hari ini", "cek transaksi kemarin")
   - Cek saldo ("ada berapa cek saldo minggu ini")
   - Ringkasan penjualan atau aktivitas EDC
   
   → Gunakan fungsi 'query_transaction_history' untuk mengambil data.

CONTOH PENGGUNAAN FUNGSI:
- "Berapa transfer hari ini?" → query_transaction_history(query_type="transfer", time_period="hari ini")
- "Lihat transaksi kemarin" → query_transaction_history(query_type="transfer", time_period="kemarin")
- "Ada berapa cek saldo minggu ini?" → query_transaction_history(query_type="balance", time_period="minggu ini")
- "Total transfer bulan lalu berapa?" → query_transaction_history(query_type="transfer", time_period="bulan lalu")

CARA MENYAMPAIKAN HASIL:
- Sebutkan jumlah transaksi dan total nominal
- Jika ada transaksi gagal, sebutkan juga
- Gunakan format yang mudah didengar: "Rp lima ratus ribu" bukan "Rp 500.000"

PENTING:
- Selalu jawab dalam Bahasa Indonesia
- Jangan baca data mentah, rangkum dengan bahasa natural
- JANGAN menjelaskan apa yang sedang kamu lakukan, langsung bicara saja""",
                "tools": tools,
                "speech_config": speech_config_obj
            }
            
            logger.info("🔌 Connecting to Gemini Live API...")
            
            async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
                logger.info("✅ Connected to Gemini Live API - Real-time mode")
                self.gemini_session = session
                
                await websocket.send(json.dumps({
                    "type": "connected",
                    "message": "Connected to Gemini Live - Start speaking!"
                }))
                
                # Create tasks for bidirectional streaming
                receive_task = asyncio.create_task(
                    self._receive_from_gemini(session, websocket),
                    name="gemini_receiver"
                )
                send_task = asyncio.create_task(
                    self._stream_audio_to_gemini(websocket, session),
                    name="audio_sender"
                )
                
                logger.info("🔄 Both streaming tasks started")
                
                # Wait for BOTH tasks - CRITICAL: using FIRST_COMPLETED but with proper handling
                done, pending = await asyncio.wait(
                    [receive_task, send_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                for task in done:
                    logger.info(f"⚠️ Task completed: {task.get_name()}")
                    exc = task.exception() if not task.cancelled() else None
                    if exc:
                        logger.error(f"   Exception: {exc}")
                
                for task in pending:
                    logger.info(f"🛑 Cancelling pending task: {task.get_name()}")
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
        
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"Client disconnected: code={e.code}, reason={e.reason}")
        except Exception as e:
            logger.error(f"Error handling voice client: {e}", exc_info=True)
        finally:
            self.is_active = False
            self.gemini_session = None
            logger.info("👤 Voice session ended")
    
    async def _stream_audio_to_gemini(self, websocket, session):
        """Stream audio from client to Gemini continuously."""
        
        logger.info("🎤 Audio streaming started")
        
        try:
            async for message in websocket:
                if not self.is_active:
                    logger.info("Session inactive, stopping audio stream")
                    break
                    
                try:
                    if isinstance(message, bytes):
                        # Send audio chunk - end_of_turn=False for continuous streaming
                        await session.send(
                            input={"data": message, "mime_type": "audio/pcm"}, 
                            end_of_turn=False
                        )
                    
                    elif isinstance(message, str):
                        data = json.loads(message)
                        msg_type = data.get("type")
                        
                        if msg_type == "text":
                            text = data.get("text", "")
                            if text:
                                logger.info(f"📤 Text input: {text}")
                                await session.send(input=text, end_of_turn=True)
                        
                        elif msg_type == "ping":
                            await websocket.send(json.dumps({"type": "pong"}))
                        
                        elif msg_type == "interrupt":
                            logger.info("⚡ User interrupt signal")
                            await session.send(input="", end_of_turn=False)
                        
                        elif msg_type == "end_turn" or msg_type == "commit":
                            logger.info("📤 End of turn signal received")
                            await session.send(input="", end_of_turn=True)
                
                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    logger.error(f"Error streaming to Gemini: {e}")
                    continue
            
            logger.info("🎤 Audio streaming ended")
        
        except websockets.exceptions.ConnectionClosed as e:
            logger.info(f"Client connection closed: code={e.code}, reason={e.reason}")
            raise
        except asyncio.CancelledError:
            logger.info("Audio stream task cancelled")
            raise
        except Exception as e:
            logger.error(f"Audio stream error: {e}")
            raise
    
    async def _receive_from_gemini(self, session, websocket):
        """Receive and forward Gemini responses to client - CONTINUOUS LOOP."""
        
        audio_chunk_count = 0
        loop_count = 0
        
        try:
            while self.is_active:
                loop_count += 1
                logger.debug(f"🔄 Starting receive loop iteration {loop_count}")
                
                try:
                    async for response in session.receive():
                        if not self.is_active:
                            logger.info("Session no longer active, stopping receiver")
                            return
                        
                        try:
                            # Audio response
                            if response.data:
                                audio_chunk_count += 1
                                audio_b64 = base64.b64encode(response.data).decode('utf-8')
                                await websocket.send(json.dumps({
                                    "type": "audio",
                                    "data": audio_b64
                                }))
                            
                            # Text/transcript
                            if response.text:
                                logger.info(f"📥 Gemini: {response.text[:80]}...")
                                await websocket.send(json.dumps({
                                    "type": "transcript",
                                    "text": response.text
                                }))
                            
                            # Turn complete
                            if response.server_content and response.server_content.turn_complete:
                                self.turn_count += 1
                                logger.info(f"✅ Turn {self.turn_count} complete ({audio_chunk_count} chunks)")
                                await websocket.send(json.dumps({
                                    "type": "turn_complete",
                                    "turn": self.turn_count
                                }))
                                audio_chunk_count = 0
                            
                            # Interrupted
                            if response.server_content and response.server_content.interrupted:
                                logger.info("⚡ Gemini was interrupted")
                                await websocket.send(json.dumps({
                                    "type": "interrupted"
                                }))
                                audio_chunk_count = 0
                            
                            # Function calls
                            if response.tool_call:
                                for fc in response.tool_call.function_calls:
                                    function_name = fc.name
                                    function_args = dict(fc.args) if fc.args else {}
                                    
                                    logger.info(f"🔧 Function: {function_name}({function_args})")
                                    
                                    await websocket.send(json.dumps({
                                        "type": "function_call",
                                        "function": function_name,
                                        "args": function_args
                                    }))
                                    
                                    if function_name == "query_transaction_history":
                                        # Run query in thread pool to not block
                                        result = await asyncio.to_thread(query_transaction_history, **function_args)
                                        
                                        # Send result to Gemini
                                        function_response = types.FunctionResponse(
                                            id=fc.id,
                                            name=fc.name,
                                            response=result
                                        )
                                        await session.send(input=function_response)
                                        logger.info(f"📤 Function response sent (count: {result.get('filtered_count', 0)})")
                                        
                                        # Send to client UI
                                        if result.get("success"):
                                            await websocket.send(json.dumps({
                                                "type": "transaction_result",
                                                "summary": result.get("summary"),
                                                "time_period": result.get("time_period"),
                                                "total_count": result.get("filtered_count", 0)
                                            }))
                        
                        except websockets.exceptions.ConnectionClosed:
                            logger.info("Client disconnected during response processing")
                            return
                        except Exception as e:
                            logger.error(f"Error processing Gemini response: {e}")
                            continue
                    
                    logger.info(f"📭 Receive iterator completed (loop {loop_count})")
                    
                    # CRITICAL: Don't break here, continue the loop for multi-turn conversation
                    if not self.is_active:
                        return
                    
                    # Small delay before next iteration
                    await asyncio.sleep(0.05)
                
                except asyncio.CancelledError:
                    logger.info("Receiver task cancelled")
                    raise
                except StopAsyncIteration:
                    logger.info("StopAsyncIteration received")
                    if self.is_active:
                        await asyncio.sleep(0.1)
                    else:
                        break
                except Exception as e:
                    logger.error(f"Error in receive loop: {type(e).__name__}: {e}")
                    if self.is_active:
                        await asyncio.sleep(0.5)
                    else:
                        break
            
            logger.info("🔚 Receive loop ended")
        
        except asyncio.CancelledError:
            logger.info("Gemini receiver cancelled gracefully")
        except Exception as e:
            logger.error(f"Gemini receive error: {type(e).__name__}: {e}")


# =============================================================================
# FASTAPI APP
# =============================================================================

app = FastAPI(title="Sekar Transaction Voice - BukuWarung Voice Assistant")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TransactionQueryRequest(BaseModel):
    query_type: str
    time_period: str
    status_filter: Optional[str] = None
    page_number: int = 0
    page_size: int = 100


@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "mode": "gemini-live real-time voice",
        "model": LIVE_MODEL,
        "port": SINGLE_PORT,
        "timestamp": datetime.now(JAKARTA_TZ).isoformat()
    }


@app.post("/api/transactions/query")
async def query_transactions(request: TransactionQueryRequest):
    result = query_transaction_history(
        query_type=request.query_type,
        time_period=request.time_period,
        status_filter=request.status_filter,
        page_number=request.page_number,
        page_size=request.page_size
    )
    return result


@app.get("/api/transactions/parse-date")
async def parse_date(time_period: str = "today"):
    start, end = parse_time_reference_with_gemini(time_period)
    return {
        "time_period": time_period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "start_date_api_format": format_datetime_for_api(start),
        "end_date_api_format": format_datetime_for_api(end)
    }


# =============================================================================
# SERVER STARTUP
# =============================================================================

async def application_server(host, port):
    """Start WebSocket server for real-time voice"""
    handler = ClientWebSocketHandler()
    
    ws_server = await websockets.serve(
        handler.handle_client, 
        host, 
        port,
        ping_interval=30,
        ping_timeout=60,
        close_timeout=10,
        max_size=10 * 1024 * 1024,
    )
    
    logger.info(f"✅ Real-time voice server on ws://{host}:{port}")
    await ws_server.wait_closed()


async def main():
    print("\n" + "="*60)
    print("🚀 SEKAR - TRANSACTION HISTORY VOICE ASSISTANT")
    print("   with Improved Real-time Conversation")
    print("="*60)
    print(f"📡 WebSocket: ws://{HOST}:{SINGLE_PORT}")
    print(f"🤖 Live Model: {LIVE_MODEL}")
    print(f"🎤 Mode: Real-time (full-duplex)")
    print("="*60 + "\n")
    
    await application_server(HOST, SINGLE_PORT)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user.")