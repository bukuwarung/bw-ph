#!/usr/bin/env python3
"""
BukuWarung Transaction History - FastAPI + Gemini Live Voice Integration
Runs both REST API (port 9000) and WebSocket server (port 8765)
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Tuple
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BUKUWARUNG_API_BASE = os.getenv("BUKUWARUNG_API_BASE", "https://api-dev.bukuwarung.com/golden-gate/api/edc")
BUKUWARUNG_TOKEN = os.getenv("BUKUWARUNG_TOKEN", "")
BUKUWARUNG_SESSION = os.getenv("BUKUWARUNG_SESSION", "")
JAKARTA_TZ = ZoneInfo("Asia/Jakarta")

# Initialize Gemini client
client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_ID = "gemini-2.0-flash-exp"
LIVE_MODEL = "gemini-2.5-flash-native-audio-preview-09-2025"

# ==================== DATE PARSER ====================

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

# ==================== TRANSACTION FUNCTIONS ====================

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

# Transaction function declarations for Gemini
TRANSACTION_FUNCTIONS = [
    {
        "name": "query_transaction_history",
        "description": "Query EDC transaction history for transfers or balance checks",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query_type": {
                    "type": "STRING",
                    "enum": ["transfer", "balance"],
                    "description": "'transfer' for money transfers, 'balance' for balance inquiries"
                },
                "time_period": {
                    "type": "STRING",
                    "description": "Natural language time: 'yesterday', 'last week', 'this month', etc."
                },
                "status_filter": {
                    "type": "STRING",
                    "enum": ["SUCCESS", "PENDING", "FAILED"],
                    "description": "Optional: filter by transaction status"
                }
            },
            "required": ["query_type", "time_period"]
        }
    }
]

# ==================== GEMINI LIVE WEBSOCKET SERVER ====================

class ClientWebSocketHandler:
    """WebSocket server for voice clients to connect"""
    
    def __init__(self):
        self.gemini_session = None
        self.client_ws = None
    
    async def handle_client(self, websocket):
        """Handle incoming WebSocket connection from web client"""
        
        logger.info(f"👤 Voice client connected: {websocket.remote_address}")
        self.client_ws = websocket
        
        try:
            # Prepare configuration
            tools = [{"function_declarations": TRANSACTION_FUNCTIONS}]
            config = {
                "response_modalities": ["AUDIO"],
                "system_instruction": """You are a friendly transaction history assistant for BukuWarung EDC merchants in Indonesia.

When users ask about their transaction history, use the query_transaction_history function and provide clear summaries.
Always use this format: "{Time period}, you made {count} transfers totaling {amount}. This included {provider breakdown}."

Be concise and natural in voice. Answer in Indonesian language.""",
                "tools": tools
            }
            
            logger.info("🔌 Connecting to Gemini Live API...")
            
            # Connect to Gemini Live using async with pattern
            async with client.aio.live.connect(model=LIVE_MODEL, config=config) as session:
                logger.info("✅ Connected to Gemini Live API")
                self.gemini_session = session
                
                # Send success to client
                await websocket.send(json.dumps({
                    "type": "connected",
                    "message": "Connected to Gemini Live API"
                }))
                
                # Run both tasks concurrently
                receiving_task = asyncio.create_task(
                    self._receive_from_gemini(session, websocket)
                )
                handling_task = asyncio.create_task(
                    self._handle_client_messages(websocket, session)
                )
                
                # Wait for either task to complete
                done, pending = await asyncio.wait(
                    [receiving_task, handling_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Cancel remaining tasks
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
        
        except Exception as e:
            logger.error(f"Error handling voice client: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            logger.info("👤 Voice client disconnected")
    
    async def _handle_client_messages(self, websocket, session):
        """Handle messages from web client"""
        
        try:
            async for message in websocket:
                try:
                    # Check if message is bytes (raw audio) or string (JSON)
                    if isinstance(message, bytes):
                        # Raw audio data from MediaRecorder (WebM/Opus format)
                        logger.info(f"📤 Sending raw audio ({len(message)} bytes)")
                        # ✅ FIX: Use input= parameter
                        await session.send(input=message, end_of_turn=True)
                    
                    elif isinstance(message, str):
                        # JSON message
                        data = json.loads(message)
                        
                        if data.get("type") == "text":
                            text = data["text"]
                            logger.info(f"📤 Sending text: {text}")
                            # ✅ FIX: Use input= parameter
                            await session.send(input=text, end_of_turn=True)
                        
                        elif data.get("type") == "audio":
                            # Base64-encoded audio
                            audio_bytes = base64.b64decode(data["data"])
                            logger.info(f"📤 Sending audio ({len(audio_bytes)} bytes)")
                            # ✅ FIX: Use input= parameter
                            await session.send(input=audio_bytes, end_of_turn=True)
                    
                    else:
                        logger.warning(f"Unknown message type: {type(message)}")
                
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON from client: {e}")
                except Exception as e:
                    logger.error(f"Error processing client message: {e}")
        
        except websockets.exceptions.ConnectionClosed:
            logger.info("Voice client disconnected")
    
    async def _receive_from_gemini(self, session, websocket):
        """Receive responses from Gemini Live"""
        
        audio_chunks = []
        
        try:
            async for response in session.receive():
                try:
                    # Handle audio response
                    if response.data:
                        audio_chunks.append(response.data)
                        logger.info(f"📥 Received audio chunk ({len(response.data)} bytes), total: {len(audio_chunks)}")
                        
                        audio_b64 = base64.b64encode(response.data).decode('utf-8')
                        await websocket.send(json.dumps({
                            "type": "response",
                            "audio": audio_b64,
                            "audio_format": "audio/pcm"
                        }))
                    
                    # Handle text response
                    if response.text:
                        logger.info(f"📥 Received text: {response.text}")
                        await websocket.send(json.dumps({
                            "type": "response",
                            "text": response.text
                        }))
                    
                    # ✅ FIX: Check for turn_complete (works for both audio-only and text responses)
                    if response.server_content and response.server_content.turn_complete:
                        logger.info(f"✅ Turn complete, sent {len(audio_chunks)} audio chunks")
                        
                        # Send turn_complete signal to frontend
                        await websocket.send(json.dumps({
                            "type": "response",
                            "turn_complete": True
                        }))
                        
                        audio_chunks = []
                    
                    # Handle tool calls
                    if response.tool_call:
                        logger.info("🔧 Tool call received")
                        
                        for fc in response.tool_call.function_calls:
                            function_name = fc.name
                            function_args = dict(fc.args) if fc.args else {}
                            
                            logger.info(f"🔧 Executing: {function_name}({function_args})")
                            
                            await websocket.send(json.dumps({
                                "type": "function_call",
                                "function": function_name,
                                "args": function_args
                            }))
                            
                            if function_name == "query_transaction_history":
                                result = query_transaction_history(**function_args)
                                
                                function_response = types.FunctionResponse(
                                    id=fc.id,
                                    name=fc.name,
                                    response=result
                                )
                                
                                # ✅ FIX: Use input= parameter
                                await session.send(input=function_response)
                                logger.info(f"📤 Sent function response for {fc.id}")
                
                except websockets.exceptions.ConnectionClosed:
                    break
                except Exception as e:
                    logger.error(f"Error processing Gemini response: {e}")
        
        except websockets.exceptions.ConnectionClosedOK:
            logger.info("Gemini connection closed normally")
        except Exception as e:
            logger.error(f"Error receiving from Gemini: {e}")

# ==================== FASTAPI APP ====================

app = FastAPI(title="Transaction History API with Voice")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    message: str
    user_id: Optional[str] = None

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
        "services": {
            "rest_api": "running",
            "websocket_voice": "running on port 8765",
            "gemini_ai": "enabled"
        },
        "timestamp": datetime.now(JAKARTA_TZ).isoformat()
    }

@app.post("/api/transactions/query")
async def query_transactions(request: TransactionQueryRequest):
    """Direct transaction query"""
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
    """Test date parsing"""
    start, end = parse_time_reference_with_gemini(time_period)
    return {
        "time_period": time_period,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "start_date_api_format": format_datetime_for_api(start),
        "end_date_api_format": format_datetime_for_api(end)
    }

# ==================== MAIN ====================

async def start_websocket_server():
    """Start WebSocket server for voice"""
    handler = ClientWebSocketHandler()
    async with websockets.serve(handler.handle_client, "0.0.0.0", 8765):
        logger.info("✅ WebSocket server running on ws://localhost:8765")
        await asyncio.Future()  # Run forever

async def main():
    """Run both FastAPI and WebSocket servers"""
    import uvicorn
    
    print("\n" + "="*60)
    print("🚀 BUKUWARUNG TRANSACTION HISTORY")
    print("="*60)
    print("📡 REST API: http://localhost:9000")
    print("📚 API Docs: http://localhost:9000/docs")
    print("🎤 Voice WebSocket: ws://localhost:8765")
    print("🤖 Powered by Gemini AI")
    print("="*60 + "\n")
    
    # Start WebSocket server in background
    websocket_task = asyncio.create_task(start_websocket_server())
    
    # Start FastAPI server (blocking)
    config = uvicorn.Config(app, host="0.0.0.0", port=9000, log_level="info")
    server = uvicorn.Server(config)
    
    try:
        await server.serve()
    except KeyboardInterrupt:
        print("\n👋 Shutting down servers...")
        websocket_task.cancel()

if __name__ == "__main__":
    asyncio.run(main())