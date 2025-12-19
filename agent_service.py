import os
import threading
import traceback
import re
from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from fastapi.security import OAuth2PasswordBearer
from fastapi.encoders import jsonable_encoder
import jwt
import requests
import asyncio
import logging
from dotenv import load_dotenv
from semantic_kernel.contents.chat_history import ChatHistory


# Import your existing components
from OrchestratorAgent import OrchestratorAgentWrapper
from IAMAssistant import IAMAssistant
from provisioning_orch_new import ProvisioningAgent
from AD_Agent import ADAgentMCP  #AD Agent using MCP
# Dashboard plugin import
from IamDashboard.iamMetrics import IAMPlugin
# Entra MCP agent (pure MCP integration)
from Entra_Agent import EntraIDMCPAgent, MCPClient, CHAT_MODEL, CHAT_MODEL_ENDPOINT, CHAT_MODEL_API_KEY, MCP_SERVER_URL
from semantic_kernel.kernel import Kernel
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion

from Okta_Agent import OktaAgentMCP  # Okta MCP-based Agent
from Saviynt_Agent import SaviyntAgentMCP  # Saviynt MCP-based Agent



# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env early so downstream modules can read them
load_dotenv()


app = FastAPI(title="IAM Assistant Service", version="1.0.0")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8501", "http://localhost:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


# Thread-safe singletons
_assistant_lock = threading.Lock()
_assistant: Optional[IAMAssistant] = None

_orchestrator_lock = threading.Lock()
_orchestrator_agent: Optional[OrchestratorAgentWrapper] = None

_provisioning_lock = threading.Lock()
_provisioning_agent: Optional[ProvisioningAgent] = None

# AD Agent singleton (MCP version)
_ad_agent_lock = threading.Lock()
_ad_agent: Optional[ADAgentMCP] = None

# Entra-only agent singleton (MCP agent)
_entra_agent_lock = threading.Lock()
_entra_agent: Optional[EntraIDMCPAgent] = None

# Okta Agent singleton (MCP version)
_okta_agent_lock = threading.Lock()
_okta_agent: Optional[OktaAgentMCP] = None


# Saviynt Agent singleton (MCP version)
_saviynt_agent_lock = threading.Lock()
_saviynt_agent: Optional[SaviyntAgentMCP] = None


# Dashboard plugin singleton
_dashboard_lock = threading.Lock()
_dashboard_plugin: Optional[IAMPlugin] = None
# Dashboard cache
_dashboard_cache_lock = threading.Lock()
_dashboard_cache: Optional[Dict[str, Any]] = None
_dashboard_cache_ts: float = 0.0
_DASHBOARD_TTL_SECONDS = int(os.getenv("DASHBOARD_CACHE_TTL", "900"))  # default 15 min


def get_assistant() -> IAMAssistant:
    global _assistant
    if _assistant is None:
        with _assistant_lock:
            if _assistant is None:
                _assistant = IAMAssistant()
    return _assistant


def get_orchestrator_agent() -> OrchestratorAgentWrapper:
    global _orchestrator_agent
    if _orchestrator_agent is None:
        with _orchestrator_lock:
            if _orchestrator_agent is None:
                _orchestrator_agent = OrchestratorAgentWrapper()
    return _orchestrator_agent


def get_provisioning_agent() -> ProvisioningAgent:
    global _provisioning_agent
    if _provisioning_agent is None:
        with _provisioning_lock:
            if _provisioning_agent is None:
                _provisioning_agent = ProvisioningAgent()
    return _provisioning_agent


async def get_ad_agent() -> ADAgentMCP:
    global _ad_agent
    if _ad_agent is None:
        with _ad_agent_lock:
            if _ad_agent is None:
                _ad_agent = ADAgentMCP()
                await _ad_agent.initialize()  # Initialize MCP connection
    return _ad_agent


async def get_entra_agent() -> EntraIDMCPAgent:
    """Thread-safe async getter that initializes the Entra MCP agent on first use."""
    global _entra_agent
    if _entra_agent is None:
        with _entra_agent_lock:
            if _entra_agent is None:
                # Create MCP client and kernel, then initialize the EntraIDMCPAgent
                mcp_client = MCPClient(MCP_SERVER_URL)
                # Instantiate agent with kernel and mcp_client; we'll initialize (connect/discover) below
                _entra_agent = EntraIDMCPAgent(mcp_client, Kernel(), "entra_provisioning")
    # If agent exists but not yet initialized (tools not discovered), initialize now
    if not getattr(_entra_agent, "available_tools", None):
        # connect MCP client
        await _entra_agent.mcp_client.connect()

        # Register LLM service on the kernel
        _entra_agent.kernel.add_service(
            AzureChatCompletion(
                service_id="entra_provisioning",
                deployment_name=CHAT_MODEL,
                endpoint=CHAT_MODEL_ENDPOINT,
                api_key=CHAT_MODEL_API_KEY,
            )
        )

        # Initialize agent (discover tools)
        await _entra_agent.initialize()

    return _entra_agent

async def get_okta_agent() -> OktaAgentMCP:
    """Thread-safe async getter that initializes the Okta MCP agent on first use."""
    global _okta_agent
    if _okta_agent is None:
        with _okta_agent_lock:
            if _okta_agent is None:
                _okta_agent = OktaAgentMCP() 

    # If not initialized yet, run initialization
    if not getattr(_okta_agent, "available_tools", None):
        await _okta_agent.initialize()

    return _okta_agent


async def get_saviynt_agent() -> SaviyntAgentMCP:
    """Thread-safe async getter that initializes the Saviynt MCP agent on first use."""
    global _saviynt_agent
    if _saviynt_agent is None:
        with _saviynt_agent_lock:
            if _saviynt_agent is None:
                _saviynt_agent = SaviyntAgentMCP()  

    # If not initialized yet, run initialization
    if not getattr(_saviynt_agent, "available_tools", None):
        await _saviynt_agent.initialize()

    return _saviynt_agent


def get_dashboard_plugin() -> IAMPlugin:
    """Thread-safe getter for the IAM Dashboard plugin."""
    global _dashboard_plugin
    if _dashboard_plugin is None:
        with _dashboard_lock:
            if _dashboard_plugin is None:
                _dashboard_plugin = IAMPlugin()
    return _dashboard_plugin


# Token verification
OPENID_CONFIG_URL = f"https://login.microsoftonline.com/{os.getenv('TENANT_ID')}/v2.0/.well-known/openid-configuration"


def get_jwk():
    try:
        response = requests.get(OPENID_CONFIG_URL)
        response.raise_for_status()
        openid_config = response.json()
        jwks_uri = openid_config['jwks_uri']
        jwks = requests.get(jwks_uri).json()
        return jwks['keys']
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching public keys: {e}")


def verify_token(token: str = Depends(oauth2_scheme)):
    try:
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token is missing")

        unverified_header = jwt.get_unverified_header(token)
        if unverified_header is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header")

        kid = unverified_header['kid']
        keys = get_jwk()

        rsa_key = {}
        for key in keys:
            if key['kid'] == kid:
                rsa_key = {
                    'kty': key['kty'],
                    'kid': key['kid'],
                    'use': key['use'],
                    'n': key['n'],
                    'e': key['e']
                }
                break

        if not rsa_key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unable to find appropriate key")

        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            options={"verify_signature": False, "verify_aud": False},
            issuer=f"https://login.microsoftonline.com/{os.getenv('TENANT_ID')}/v2.0"
        )
        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Token verification failed: {str(e)}")


# --- Models ---
class ChatRequest(BaseModel):
    thread_id: str
    message: str

class ThreadResponse(BaseModel):
    thread_id: str

class ChatResponse(BaseModel):
    reply: str

class OrchestratorChatRequest(BaseModel):
    thread_id: str
    message: str
    chat_history: List[Dict[str, str]]

class OrchestratorChatResponse(BaseModel):
    action: str
    result: str

# Entra Service Models
"""
Legacy Entra request/response models removed; using EntraAgentChatRequest/Response instead.
"""

class EntraAgentChatRequest(BaseModel):
    thread_id: str
    message: str
    chat_history: List[Dict[str, str]]

class EntraAgentChatResponse(BaseModel):
    action: str
    result: str

# ADD NEW AD MODELS:
class ADProvisioningRequest(BaseModel):
    thread_id: str
    message: str

class ADProvisioningResponse(BaseModel):
    action: str
    result: str
    agent: str

class OktaAgentChatRequest(BaseModel):
    thread_id: str
    message: str
    chat_history: List[Dict[str, str]]

class OktaAgentChatResponse(BaseModel):
    action: str
    result: str


class SaviyntAgentChatRequest(BaseModel):
    thread_id: str
    message: str
    chat_history: List[Dict[str, str]]


class SaviyntAgentChatResponse(BaseModel):
    action: str
    result: str

class UserCreateRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    user_principal_name: str = Field(..., pattern=r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
    password: str = Field(..., min_length=8)

class GroupCreateRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    mail_nickname: str = Field(..., min_length=1, max_length=100)
    is_security_enabled: bool = True


# Helper functions for Entra operations
"""
Removed legacy Entra intent parsing and execution helpers.
"""


# Health check
@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# Existing endpoints
@app.post("/thread", response_model=ThreadResponse, status_code=status.HTTP_201_CREATED)
def create_thread(token: str = Depends(verify_token)):
    try:
        assistant = get_assistant()
        tid = assistant.create_thread()
        return ThreadResponse(thread_id=tid)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to create thread: {e}")


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, token: str = Depends(verify_token)):
    try:
        assistant = get_assistant()
        reply = assistant.chat_on_thread(thread_id=req.thread_id, user_query=req.message)
        return ChatResponse(reply=reply)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Chat failed: {e}")


@app.post("/orchestrator/thread", response_model=ThreadResponse, status_code=status.HTTP_201_CREATED)
def create_orchestrator_thread(token: str = Depends(verify_token)):
    try:
        tid = f"orch-{os.urandom(4).hex()}"
        return ThreadResponse(thread_id=tid)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to create orchestrator thread: {e}")


@app.post("/orchestrator/chat", response_model=OrchestratorChatResponse)
async def orchestrator_chat(req: OrchestratorChatRequest, token: str = Depends(verify_token)):
    try:
        orchestrator_agent = get_orchestrator_agent()
        response = await orchestrator_agent.chat(
            thread_id=req.thread_id,
            user_message=req.message,
            chat_history=req.chat_history,
        )
        return response
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Orchestrator chat failed: {e}")


"""
Removed legacy /entra/thread and /entra/chat endpoints in favor of /entra/agent/*.
"""


# Entra-only Agent Orchestration Endpoints
@app.post("/entra/agent/thread", response_model=ThreadResponse, status_code=status.HTTP_201_CREATED)
def create_entra_agent_thread(token: str = Depends(verify_token)):
    try:
        thread_id = f"entra-agent-{os.urandom(4).hex()}"
        return ThreadResponse(thread_id=thread_id)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to create Entra Agent thread: {e}")


@app.post("/entra/agent/chat", response_model=EntraAgentChatResponse)
async def entra_agent_chat(req: EntraAgentChatRequest, token: str = Depends(verify_token)):
    try:
        entra_agent = await get_entra_agent()

        # Convert incoming simple chat_history (list of dicts) into semantic-kernel ChatHistory
        sk_history = ChatHistory()
        from semantic_kernel.contents.chat_message_content import ChatMessageContent
        from semantic_kernel.contents.utils.author_role import AuthorRole
        for msg in req.chat_history:
            role = AuthorRole.USER if msg.get("role") == "user" else AuthorRole.ASSISTANT
            sk_history.messages.append(ChatMessageContent(role=role, content=msg.get("content", "")))

        # Process the message via the MCP-based Entra agent
        result_text = await entra_agent.process_message(req.message, sk_history)

        return EntraAgentChatResponse(action="entra_provision", result=result_text)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Entra Agent chat failed: {e}")


# NEW: AD Provisioning Service Endpoints
@app.post("/ad/thread", response_model=ThreadResponse, status_code=status.HTTP_201_CREATED)
async def create_ad_thread(token: str = Depends(verify_token)):
    try:
        thread_id = f"ad-{os.urandom(4).hex()}"
        # Initialize chat history for this thread
        ad_agent = await get_ad_agent()
        await ad_agent.create_thread(thread_id)
        return ThreadResponse(thread_id=thread_id)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to create AD thread: {e}")


@app.post("/ad/chat", response_model=ADProvisioningResponse)
async def ad_provisioning_chat(req: ADProvisioningRequest, token: str = Depends(verify_token)):
    try:
        user_info = token.get('preferred_username', 'unknown')
        logger.info(f"User {user_info} requested AD operation: {req.message}")
        
        ad_agent = await get_ad_agent()
        # Retrieve the persistent chat history for the thread (creates it if missing)
        chat_history = await ad_agent.get_thread_history(req.thread_id)

        # Append user's message to history
        try:
            # ChatHistory provides helpers used by the agent run loop
            chat_history.add_user_message(req.message)
        except Exception:
            # Fallback: try appending raw ChatMessageContent
            from semantic_kernel.contents.chat_message_content import ChatMessageContent
            from semantic_kernel.contents.utils.author_role import AuthorRole
            chat_history.messages.append(ChatMessageContent(role=AuthorRole.USER, content=req.message))

        # Process request
        response = await ad_agent._process_user_request(req.message, chat_history)

        # Append assistant response to history
        try:
            chat_history.add_assistant_message(response)
        except Exception:
            from semantic_kernel.contents.chat_message_content import ChatMessageContent
            from semantic_kernel.contents.utils.author_role import AuthorRole
            chat_history.messages.append(ChatMessageContent(role=AuthorRole.ASSISTANT, content=response))

        logger.info(f"AD operation completed for user {user_info}")
        
        return ADProvisioningResponse(
            action="ad_provision",
            result=response,
            agent="AD_MCP_Agent"
        )
    except Exception as e:
        logger.error(f"AD provisioning chat failed: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"AD provisioning chat failed: {e}")
    
@app.post("/okta/agent/thread", response_model=ThreadResponse, status_code=status.HTTP_201_CREATED)
def create_okta_agent_thread(token: str = Depends(verify_token)):
    try:
        thread_id = f"okta-agent-{os.urandom(4).hex()}"
        return ThreadResponse(thread_id=thread_id)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to create Okta Agent thread: {e}")

@app.post("/okta/agent/chat", response_model=OktaAgentChatResponse)
async def okta_agent_chat(req: OktaAgentChatRequest, token: str = Depends(verify_token)):
    try:
        okta_agent = await get_okta_agent()

        # Convert incoming simple chat history → SK ChatHistory
        sk_history = ChatHistory()
        from semantic_kernel.contents.chat_message_content import ChatMessageContent
        from semantic_kernel.contents.utils.author_role import AuthorRole

        for msg in req.chat_history:
            role = AuthorRole.USER if msg.get("role") == "user" else AuthorRole.ASSISTANT
            sk_history.messages.append(ChatMessageContent(role=role, content=msg.get("content", "")))

        # Process user message
        result_text = await okta_agent.process_message(req.message, sk_history)

        return OktaAgentChatResponse(action="okta_provision", result=result_text)

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Okta Agent chat failed: {e}")


@app.post("/saviynt/agent/thread", response_model=ThreadResponse, status_code=status.HTTP_201_CREATED)
def create_saviynt_agent_thread(token: str = Depends(verify_token)):
    try:
        thread_id = f"saviynt-agent-{os.urandom(4).hex()}"
        return ThreadResponse(thread_id=thread_id)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to create Saviynt Agent thread: {e}")


@app.post("/saviynt/agent/chat", response_model=SaviyntAgentChatResponse)
async def saviynt_agent_chat(req: SaviyntAgentChatRequest, token: str = Depends(verify_token)):
    try:
        saviynt_agent = await get_saviynt_agent()

        # Convert incoming simple chat history → SK ChatHistory
        sk_history = ChatHistory()
        from semantic_kernel.contents.chat_message_content import ChatMessageContent
        from semantic_kernel.contents.utils.author_role import AuthorRole

        for msg in req.chat_history:
            role = AuthorRole.USER if msg.get("role") == "user" else AuthorRole.ASSISTANT
            sk_history.messages.append(ChatMessageContent(role=role, content=msg.get("content", "")))

        # Process user message
        result_text = await saviynt_agent.process_message(req.message, sk_history)

        return SaviyntAgentChatResponse(action="saviynt_provision", result=result_text)

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Saviynt Agent chat failed: {e}")


# IAM Dashboard Endpoints
@app.get("/dashboard/summary", response_model=dict)
async def get_iam_dashboard_summary(token: str = Depends(verify_token), refresh: Optional[int] = 0):
    """Builds and returns the IAM dashboard metrics by aggregating Entra and AD data."""
    try:
        use_cache = not bool(refresh)
        global _dashboard_cache, _dashboard_cache_ts
        # Serve from cache if valid
        if use_cache:
            with _dashboard_cache_lock:
                import time as _t
                if _dashboard_cache is not None and (_t.time() - _dashboard_cache_ts) < _DASHBOARD_TTL_SECONDS:
                    return {"success": True, "data": _dashboard_cache}

        # Build fresh
        plugin = get_dashboard_plugin()
        raw_data = await plugin.build_iam_dashboard()
        data = jsonable_encoder(raw_data)

        # Update cache
        with _dashboard_cache_lock:
            import time as _t
            _dashboard_cache = data
            _dashboard_cache_ts = _t.time()

        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"Failed to build IAM dashboard: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to build IAM dashboard: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)