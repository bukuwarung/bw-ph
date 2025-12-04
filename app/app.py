#!/usr/bin/env python3
"""
BukuWarung Transaction History - FastAPI + Gemini Live Voice Integration
Unified Server running REST API and WebSocket on Port 8765 using socket sharing.
"""

from fastapi import FastAPI
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
import uvicorn
import uvicorn.protocols.http.h11_impl
import socket # New import for socket sharing

# Import Gemini client library
from google import genai
from google.genai import types

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
# ==================== CONFIGURATION ====================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
BUKUWARUNG_API_BASE = os.getenv("BUKUWARUNG_API_BASE", "https://api-dev.bukuwarung.com/golden-gate/api/edc")
BUKUWARUNG_TOKEN = os.getenv("BUKUWARUNG_TOKEN", "")
BUKUWARUNG_SESSION = os.getenv("BUKUWARUNG_SESSION", "")
JAKARTA_TZ = ZoneInfo("Asia/Jakarta")

# Unified Server Port
SINGLE_PORT = 8765
HOST = "0.0.0.0"

# Initialize Gemini client
client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_ID = "gemini-2.0-flash-exp"
LIVE_MODEL = "gemini-2.5-flash-native-audio-preview-09-2025"

# ==================== TRANSACTION & DATE PARSING FUNCTIONS ====================

# [Keep your existing PARSE_DATE_RANGE_FUNCTION, calculate_date_range, 
#  parse_time_reference_with_gemini, format_datetime_for_api, 
#  summarize_transactions, query_transaction_history, and TRANSACTION_FUNCTIONS 
#  here without modification.]

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

# ==================== GEMINI LIVE WEBSOCKET HANDLER ====================

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
                
                done, pending = await asyncio.wait(
                    [receiving_task, handling_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
        
        except Exception as e:
            logger.error(f"Error handling voice client: {e}")
        
        finally:
            logger.info("👤 Voice client disconnected")
    
    async def _handle_client_messages(self, websocket, session):
        """Handle messages from web client (Streaming logic)"""
        
        try:
            async for message in websocket:
                try:
                    if isinstance(message, bytes):
                        # Audio Chunk: end_of_turn=False for streaming
                        await session.send(
                            input={"data": message, "mime_type": "audio/pcm"}, 
                            end_of_turn=False 
                        )
                    
                    elif isinstance(message, str):
                        data = json.loads(message)
                        
                        if data.get("type") == "commit":
                            # Stop speaking signal: end_of_turn=True to trigger response
                            await session.send(input="", end_of_turn=True)
                            logger.info("✅ Sent COMMIT signal to Gemini")

                        elif data.get("type") == "text":
                            text = data["text"]
                            logger.info(f"📤 Sending text: {text}")
                            await session.send(input=text, end_of_turn=True)
                
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON from client: {e}")
                except Exception as e:
                    logger.error(f"Error processing client message: {e}")
        
        except websockets.exceptions.ConnectionClosed:
            logger.info("Voice client disconnected")
    
    async def _receive_from_gemini(self, session, websocket):
        """Receive responses from Gemini Live (Streaming)"""
        
        audio_chunks = []
        
        try:
            async for response in session.receive():
                try:
                    if response.data:
                        audio_chunks.append(response.data)
                        audio_b64 = base64.b64encode(response.data).decode('utf-8')
                        await websocket.send(json.dumps({
                            "type": "response",
                            "audio": audio_b64,
                            "audio_format": "audio/pcm"
                        }))
                    
                    if response.text:
                        logger.info(f"📥 Received text: {response.text}")
                        await websocket.send(json.dumps({
                            "type": "response",
                            "text": response.text
                        }))
                    
                    if response.server_content and response.server_content.turn_complete:
                        logger.info(f"✅ Turn complete, sent {len(audio_chunks)} audio chunks")
                        await websocket.send(json.dumps({
                            "type": "response",
                            "turn_complete": True
                        }))
                        audio_chunks = []
                    
                    if response.tool_call:
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
            "rest_api": f"running on port {SINGLE_PORT}",
            "websocket_voice": f"running on port {SINGLE_PORT}",
            "gemini_ai": "enabled"
        },
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

# ==================== UNIFIED SERVER STARTUP (FIXED) ====================

async def handle_http_request(reader, writer):
    """Handle standard HTTP requests (FastAPI) on the asyncio event loop."""
    try:
        config = uvicorn.Config(app, lifespan="off", log_level="warning")
        protocol = uvicorn.protocols.http.h11_impl.H11Protocol(config=config, server_state={})
        transport = type('Transport', (object,), {'close': writer.close})()
        protocol.connection = transport
        
        while True:
            data = await reader.read(65536)
            if not data:
                break
            protocol.data_received(data)
            if protocol.h11_state == 'END':
                break

        response = await protocol.to_future
        writer.write(response)
        await writer.drain()
    except Exception as e:
        logger.error(f"HTTP handling error: {e}")
    finally:
        writer.close()


async def application_server(host, port):
    """
    Starts two servers (HTTP and WebSocket) on the same host and port 
    by binding to a single, shared socket to prevent OS error 48.
    """
    handler = ClientWebSocketHandler()
    loop = asyncio.get_running_loop()

    # 1. Create a single TCP socket and configure it for reuse
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) 
    sock.bind((host, port))
    sock.listen(500) # Listen on the socket

    # 2. Start the HTTP server using the shared socket
    http_server = await loop.create_server(
        lambda: asyncio.StreamReaderProtocol(
            asyncio.StreamReader(loop=loop), handle_http_request, loop=loop
        ),
        sock=sock,
    )

    # 3. Start the WebSocket server using the same shared socket
    ws_server = await websockets.serve(
        handler.handle_client, 
        sock=sock, 
        reuse_port=False 
    )
    
    logger.info(f"✅ Unified server (HTTP/WS) running on http://{host}:{port}")
    
    async with http_server, ws_server:
        await asyncio.gather(http_server.serve_forever(), ws_server.serve_forever())


async def main():
    """Run the unified server startup."""
    
    print("\n" + "="*60)
    print("🚀 BUKUWARUNG TRANSACTION HISTORY - UNIFIED SERVER")
    print("="*60)
    print(f"📡 REST/WS Unified Port: http://{HOST}:{SINGLE_PORT}")
    print(f"📚 API Docs: http://{HOST}:{SINGLE_PORT}/docs (access might require direct HTTP client)")
    print("🤖 Powered by Gemini AI")
    print("="*60 + "\n")
    
    try:
        await application_server(HOST, SINGLE_PORT)
    except KeyboardInterrupt:
        print("\n👋 Shutting down servers...")

if __name__ == "__main__":
    asyncio.run(main())