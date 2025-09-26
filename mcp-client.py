from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import subprocess
import asyncio
from typing import Dict, Any, List, Optional
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="MCP Bridge API", description="Bridge between MCP Server and UI")

# Enable CORS for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this properly for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models for request/response
class MCPRequest(BaseModel):
    method: str
    params: Optional[Dict[str, Any]] = None

class MCPResponse(BaseModel):
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None

class ToolCall(BaseModel):
    tool_name: str
    parameters: Dict[str, Any]

class MCPConfig:
    def __init__(self, config_path: str = "mcp_config.json"):
        self.config_path = config_path
        self.config = self.load_config()
    
    def load_config(self):
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load MCP config: {e}")
            return {}

# Global config instance
mcp_config = MCPConfig()

class MCPClient:
    def __init__(self):
        self.process = None
        self.connected = False
    
    async def connect(self):
        """Connect to MCP server using stdio"""
        try:
            # Get server command from config
            server_config = mcp_config.config.get('mcpServers', {})
            if not server_config:
                raise Exception("No MCP server configuration found")
            
            # Assume first server in config (adjust as needed)
            server_name = list(server_config.keys())[0]
            server_info = server_config[server_name]
            
            command = server_info.get('command')
            args = server_info.get('args', [])
            
            if not command:
                raise Exception("No command specified in MCP config")
            
            # Start the MCP server process
            full_command = [command] + args
            self.process = await asyncio.create_subprocess_exec(
                *full_command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Initialize MCP connection
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "roots": {"listChanged": True},
                        "sampling": {}
                    },
                    "clientInfo": {
                        "name": "fastapi-mcp-bridge",
                        "version": "1.0.0"
                    }
                }
            }
            
            await self.send_request(init_request)
            self.connected = True
            logger.info("Connected to MCP server")
            
        except Exception as e:
            logger.error(f"Failed to connect to MCP server: {e}")
            raise
    
    async def send_request(self, request: dict) -> dict:
        """Send request to MCP server and get response"""
        if not self.process:
            raise Exception("MCP server not connected")
        
        try:
            # Send request
            request_json = json.dumps(request) + '\n'
            self.process.stdin.write(request_json.encode())
            await self.process.stdin.drain()
            
            # Read response
            response_line = await self.process.stdout.readline()
            response = json.loads(response_line.decode().strip())
            
            return response
            
        except Exception as e:
            logger.error(f"Error communicating with MCP server: {e}")
            raise
    
    async def disconnect(self):
        """Disconnect from MCP server"""
        if self.process:
            self.process.terminate()
            await self.process.wait()
            self.connected = False

# Global MCP client instance
mcp_client = MCPClient()

@app.on_event("startup")
async def startup_event():
    """Initialize MCP connection on startup"""
    try:
        await mcp_client.connect()
    except Exception as e:
        logger.error(f"Failed to start MCP connection: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up MCP connection on shutdown"""
    await mcp_client.disconnect()

@app.get("/")
async def root():
    return {"message": "MCP Bridge API is running", "connected": mcp_client.connected}

@app.get("/tools")
async def list_tools():
    """List available tools from MCP server"""
    try:
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        }
        
        response = await mcp_client.send_request(request)
        
        if "error" in response:
            raise HTTPException(status_code=500, detail=response["error"])
        
        return MCPResponse(success=True, result=response.get("result", {}))
        
    except Exception as e:
        return MCPResponse(success=False, error=str(e))

@app.post("/tools/call")
async def call_tool(tool_call: ToolCall):
    """Call a specific tool with parameters"""
    try:
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": tool_call.tool_name,
                "arguments": tool_call.parameters
            }
        }
        
        response = await mcp_client.send_request(request)
        
        if "error" in response:
            raise HTTPException(status_code=500, detail=response["error"])
        
        return MCPResponse(success=True, result=response.get("result", {}))
        
    except Exception as e:
        return MCPResponse(success=False, error=str(e))

@app.post("/mcp/request")
async def send_mcp_request(mcp_request: MCPRequest):
    """Send arbitrary MCP request"""
    try:
        request = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": mcp_request.method,
            "params": mcp_request.params or {}
        }
        
        response = await mcp_client.send_request(request)
        
        if "error" in response:
            raise HTTPException(status_code=500, detail=response["error"])
        
        return MCPResponse(success=True, result=response.get("result", {}))
        
    except Exception as e:
        return MCPResponse(success=False, error=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "mcp_connected": mcp_client.connected,
        "config_loaded": bool(mcp_config.config)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)