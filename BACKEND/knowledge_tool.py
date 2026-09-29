import os
import logging
from typing import Any
import httpx

from livekit.agents import function_tool
from app.services.conversation_state import ACTIVE_CALLS

logger = logging.getLogger("callinggen.knowledge_tool")

@function_tool(
    description="""
Search the company's verified knowledge base to answer customer questions accurately.
Use this tool whenever the customer asks about:
- Products, features, specifications, or catalogs
- Services offered, packages, or solutions
- Pricing, rates, discounts, or payment terms
- Return policy, refund policy, warranty, or guarantees
- Business hours, office locations, contact info, or website
- FAQs, rules, guidelines, or company background

Do not hallucinate or make up company facts. If you do not know the exact answer, search the knowledge base.
"""
)
async def knowledge_search(
    query: str,
) -> str:
    """Search company knowledge base and return relevant text excerpts."""
    clean_query = (query or "").strip()
    if not clean_query:
        return "Please specify a question or topic to search."

    print(f"[knowledge_tool] Searching knowledge base for: '{clean_query}'")

    # Resolve active call and user/tenant ID + attached knowledge doc IDs
    user_id = None
    target_doc_ids = None
    for room_name, state in ACTIVE_CALLS.items():
        if state and not state.get("finishing"):
            user_id = state.get("user_id")
            target_doc_ids = state.get("knowledge_document_ids")
            break

    # If not directly in state, query DB for the active call
    if not user_id and ACTIVE_CALLS:
        try:
            from app.database import AsyncSessionLocal
            from app.models.call import Call
            from app.models.job import Job
            from app.models.campaign import Campaign
            from sqlalchemy import select

            first_room = list(ACTIVE_CALLS.keys())[0]
            call_id = int(first_room.rsplit("-", 1)[-1]) if "-" in first_room else -1

            if call_id != -1:
                async with AsyncSessionLocal() as db:
                    call = await db.get(Call, call_id)
                    if call:
                        user_id = call.user_id
                        if not user_id and call.job_id:
                            job = await db.get(Job, call.job_id)
                            if job:
                                user_id = job.user_id
                                if job.campaign_id:
                                    camp = await db.get(Campaign, job.campaign_id)
                                    if camp and camp.knowledge_document_ids:
                                        target_doc_ids = camp.knowledge_document_ids
        except Exception as e:
            print(f"[knowledge_tool] DB lookup warning: {e}")

    # Fallback to user_id=1 if single-tenant / local development
    target_user_id = user_id or 1

    try:
        from app.database import AsyncSessionLocal
        from app.services.knowledge_service import KnowledgeService

        async with AsyncSessionLocal() as db:
            results = await KnowledgeService.search_knowledge(
                db=db,
                user_id=target_user_id,
                query=clean_query,
                document_ids=target_doc_ids,
                top_k=3,
                threshold=0.15,
            )

            if not results:
                print(f"[knowledge_tool] No matching knowledge chunks found for '{clean_query}' (user_id={target_user_id}, doc_ids={target_doc_ids})")
                return "No specific company document was found for that question. Let the customer know you will check with the team."

            # Format top relevant chunks
            formatted_chunks = []
            for r in results:
                title = r.get("title", "Document")
                content = r.get("content", "").strip()
                formatted_chunks.append(f"[{title}]\n{content}")

            combined_knowledge = "\n\n---\n\n".join(formatted_chunks)
            print(f"[knowledge_tool] Found {len(results)} chunks. Returning to LLM.")
            return (
                f"VERIFIED COMPANY KNOWLEDGE BASE FACTS FOR '{clean_query}':\n"
                f"{combined_knowledge}\n\n"
                "INSTRUCTION: Use the verified facts above to answer the caller's question clearly, concisely, and naturally in 1-2 spoken sentences."
            )

    except Exception as e:
        print(f"[knowledge_tool] Error performing knowledge search: {e}")
        return "I could not retrieve the company information at this moment."
