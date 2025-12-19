import os
import logging
import json
from semantic_kernel.functions import kernel_function
from dotenv import load_dotenv
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import ConnectionType
from azure.identity import DefaultAzureCredential
from azure.ai.projects.models import AzureAISearchTool
from semantic_kernel.agents import ChatCompletionAgent
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
from semantic_kernel.contents.chat_message_content import ChatMessageContent
from semantic_kernel.contents.chat_history import ChatHistory
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
from semantic_kernel.contents.utils.author_role import AuthorRole
from semantic_kernel.kernel import Kernel
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from azure.identity import ClientSecretCredential
from azure.core.exceptions import ServiceResponseError

 
load_dotenv()
credential=ClientSecretCredential(
    tenant_id=os.environ["TENANT_ID"],
    client_id=os.environ["CLIENT_ID_BACKEND"],
    client_secret=os.environ["CLIENT_SECRET_BACKEND"]
)
class IAMAssistant:
    """
    IAM Assistant with explicit thread control.
    - Initialize once (agent + tools)
    - Create threads per user/session
    - Send messages on a given thread
    """
    def __init__(self):
        # Initialize Azure AI Project and Search tool once
        self.project_client = AIProjectClient.from_connection_string(
            # credential=DefaultAzureCredential(),
            credential=credential,
            conn_str=os.environ["AIPROJECT_CONNECTION_STRING"],
        )

        # Find Cognitive Search connection
        conn_list = self.project_client.connections.list()
        conn_id = next(
            (conn.id for conn in conn_list if conn.connection_type == "CognitiveSearch"),
            None
        )
        if not conn_id:
            raise RuntimeError("No Cognitive Search connection found for IAM documents.")

        # Configure Azure AI Search Tooll
        self.ai_search = AzureAISearchTool(index_connection_id=conn_id, index_name="end-user-rag")

        # Create the agent once
        self.iam_agent = self.project_client.agents.create_agent(
            model="gpt-4.1-nano",  # ensure this model exists in your Azure project
            name="IAM Assistant",
            instructions=
                """You are an expert assistant focused exclusively on assisting users with tasks related to Identity and Access Management in Entra ID. 
You should ONLY use the provided IAM documentation for answering user queries from "tool_resources" (end-user-rag). When asked a query:
1. **Search the documentation**: Use the "Azure AI search tool" to retrieve relevant content from the IAM documentation for the user query.
2. **No external sources**: Do not use the web or any external sources to generate answers.
3. **Refuse unsupported queries**: If you cannot find relevant information in the documentation, say: "I don't know the answer to that. My responses are based solely on the IAM documentation."
4. **Provide clear and concise responses**: If the documentation contains information, respond with the most relevant content. If not, say: "The information is not available in the documentation."
5. **Do not guess or make inferences**: Only answer based on what’s available in the documentation. Give the result as it is in the documents DO NOT summarize, DO NOT exclude essential information.
6. **Give presentable answers**: Format your answers in a user-friendly manner, using bullet points or numbered lists where appropriate.
Always ensure the responses are professional and accurate."""
            ,
            tools=self.ai_search.definitions,
            tool_resources=self.ai_search.resources,
        )

    def create_thread(self) -> str:
        """Create and return a new thread id."""
        thread = self.project_client.agents.create_thread()
        return thread.id

    def chat_on_thread(self, thread_id: str, user_query: str) -> str:
        """
        Sends a user query to the IAM assistant on the specified thread and returns its response.

        If the assistant responds with "I don't know..." or "The information is not available...",
        the function automatically retries the query in a clean (temporary) thread once.

        Steps:
        1. Add user message to the main thread.
        2. Process it via IAM agent.
        3. If response says “I don’t know…”, re-run the same query in a new thread.
        4. Return the better response (and store it in the main thread for continuity).
        """

        
        try:
            # Step 1: Add the user message to the current thread
            self.project_client.agents.create_message(
                thread_id=thread_id,
                role="user",
                content=user_query,
            )

            # Step 2: Process the run with the IAM agent
            run = self.project_client.agents.create_and_process_run(
                thread_id=thread_id,
                assistant_id=self.iam_agent.id
            )

            # Step 3: Retrieve assistant's latest message
            messages = self.project_client.agents.list_messages(thread_id=thread_id)
            last_message = messages.get_last_text_message_by_role("assistant")
            response_text = (
                last_message.text.value if last_message and last_message.text
                else "🤖 No response received."
            )

            # Step 4: If the model indicates lack of information, trigger fallback
            if "I don't know the answer" in response_text or "The document does not" in response_text:
                logging.info("[Fallback Triggered] Retrying query in temporary clean thread...")

                # Create a new clean temporary thread
                temp_thread = self.project_client.agents.create_thread()

                # Add the same user query to the temporary thread
                self.project_client.agents.create_message(
                    thread_id=temp_thread.id,
                    role="user",
                    content=user_query,
                )

                # Run the assistant again in the clean thread
                temp_run = self.project_client.agents.create_and_process_run(
                    thread_id=temp_thread.id,
                    assistant_id=self.iam_agent.id,
                )

                # Retrieve the fallback response
                temp_messages = self.project_client.agents.list_messages(thread_id=temp_thread.id)
                fallback_message = temp_messages.get_last_text_message_by_role("assistant")
                fallback_response = (
                    fallback_message.text.value
                    if fallback_message and fallback_message.text
                    else "🤖 No fallback response received."
                )

                # Store the recovered response in the original thread (for continuity)
                self.project_client.agents.create_message(
                    thread_id=thread_id,
                    role="assistant",
                    content=f"[Recovered Answer]\n{fallback_response}",
                )

                return fallback_response

            # Step 5: Normal case — return main response
            return response_text

        except ServiceResponseError as e:
            # Handle transient service connection failures
            if "Remote end closed connection without response" in str(e):
                return "❌ Connection to Azure AI service lost. Please try again."
            return f"❌ Service error: {e}"

        except Exception as e:
            # Generic fallback for unexpected errors
            logging.error(f"❌ Unexpected error: {e}")
            return f"❌ Unexpected error occurred: {e}"
