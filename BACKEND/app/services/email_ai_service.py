import os
import json
import re
from openai import AsyncOpenAI
from app.schemas.email_ai import EmailAIGenerateRequest, EmailAIGenerateResponse


class EmailAIService:
    @classmethod
    async def generate_email_content(
        cls, request: EmailAIGenerateRequest, user_company_name: str | None = None
    ) -> EmailAIGenerateResponse:
        """
        Generates structured email content using Deepseek / OpenAI API.
        Guarantees high-converting, professional marketing email structure
        compatible with CallingGen's template wrapper.
        """
        company_ref = user_company_name or "{{company}}"
        
        system_prompt = (
            "You are an expert email marketing copywriter and conversion rate optimization specialist for CallingGen AI. "
            "Your task is to write high-converting, professional, and visually engaging marketing emails based on user instructions.\n\n"
            "CRITICAL RULES:\n"
            "1. Output MUST BE strictly valid JSON matching this exact structure with NO markdown fences around it:\n"
            "{\n"
            '  "subject": "Catchy email subject line with emojis where suitable and personalization token {{name}} or {{company}}",\n'
            '  "heading": "Clear, engaging H2 heading for the email hero section",\n'
            '  "body": "Clean semantic HTML content (using only <h2>, <p>, <strong>, <ul>, <li>, and <a> elements) containing the complete message body, key bullet points, and CTA button.",\n'
            '  "cta_text": "Action-oriented button label (e.g. Claim 20% Discount &rarr;, Schedule a Demo &rarr;)",\n'
            '  "cta_link": "https://callinggen.in/demo or destination URL"\n'
            "}\n"
            "2. Preserve personalization variables: Use {{name}} for customer's name, {{company}} for company name, and {{email}} for email.\n"
            "3. In the 'body' field, write rich semantic HTML with proper spacing (<p><br></p>), clear value propositions in <ul><li>, and a centered CTA button styled with: style=\"background-color: #2563eb; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block;\".\n"
            "4. Match the requested tone and marketing goal precisely.\n"
            "5. Do NOT include outer <html>, <head>, or <body> tags in the 'body' field — only inner content elements."
        )

        user_content_prompt = f"User Request/Instructions: {request.prompt}\n"
        if request.tone:
            user_content_prompt += f"Desired Tone: {request.tone}\n"
        if request.category:
            user_content_prompt += f"Campaign Category: {request.category}\n"
        if request.action and request.action != "generate":
            user_content_prompt += f"Refinement Action: {request.action}\n"
        if request.current_subject:
            user_content_prompt += f"Current Subject: {request.current_subject}\n"
        if request.current_heading:
            user_content_prompt += f"Current Heading: {request.current_heading}\n"
        if request.current_body:
            user_content_prompt += f"Current Body Content to refine:\n{request.current_body}\n"
        if request.current_cta_text:
            user_content_prompt += f"Current CTA Text: {request.current_cta_text}\n"

        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        
        # 1. Try Deepseek / OpenAI API
        if deepseek_key or openai_key:
            try:
                if deepseek_key:
                    client = AsyncOpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com/v1")
                    model = "deepseek-chat"
                else:
                    client = AsyncOpenAI(api_key=openai_key)
                    model = "gpt-4o-mini"

                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content_prompt},
                    ],
                    temperature=0.7,
                    max_tokens=1500,
                )

                raw_text = response.choices[0].message.content or "{}"
                # Clean up any potential markdown json fences
                raw_text = re.sub(r"^```json\s*", "", raw_text.strip(), flags=re.IGNORECASE)
                raw_text = re.sub(r"^```\s*", "", raw_text.strip())
                raw_text = re.sub(r"\s*```$", "", raw_text.strip())

                data = json.loads(raw_text)
                
                subject = data.get("subject") or f"Special Announcement from {company_ref}"
                heading = data.get("heading") or f"Exciting Update from {company_ref}"
                body = data.get("body") or "<p>Hello <strong>{{name}}</strong>,</p><p>We are excited to share an update with you.</p>"
                cta_text = data.get("cta_text") or "Explore Now &rarr;"
                cta_link = data.get("cta_link") or "https://callinggen.in"

                return EmailAIGenerateResponse(
                    subject=subject,
                    heading=heading,
                    body=body,
                    cta_text=cta_text,
                    cta_link=cta_link,
                    tone=request.tone or "Professional",
                )
            except Exception as e:
                print(f"[EmailAIService] LLM API generation error: {e}. Falling back to dynamic heuristic generation.")

        # 2. Heuristic Dynamic Fallback Generator (if offline or API key missing)
        return cls._generate_fallback(request, company_ref)

    @classmethod
    def _generate_fallback(
        cls, request: EmailAIGenerateRequest, company_ref: str
    ) -> EmailAIGenerateResponse:
        """Generates dynamic tailored marketing templates if external API is unreachable."""
        prompt_lower = request.prompt.lower()
        tone = request.tone or "Professional"

        if "discount" in prompt_lower or "promo" in prompt_lower or "offer" in prompt_lower or "20%" in prompt_lower:
            subject = f"Exclusive 20% Discount for {{name}} — Limited Time Offer from {company_ref} 🎉"
            heading = f"Special 20% Savings on Your Next Order with {company_ref}"
            cta_text = "Claim Your 20% Discount &rarr;"
            body = f"""<h2><strong>A Special Offer Just for You, {{{{name}}}}</strong></h2>
<p><br></p>
<p>Hello <strong>{{{{name}}}}</strong>,</p>
<p><br></p>
<p>We wanted to say thank you for being a valued part of the <strong>{company_ref}</strong> community. As a token of our appreciation, we are offering an exclusive <strong>20% discount</strong> on our services for a limited time.</p>
<p><br></p>
<p><strong>Why Partner With Us:</strong></p>
<ul>
    <li><strong>⚡ Rapid Deployment:</strong> Seamless setup and high-speed automation out of the box.</li>
    <li><strong>🎯 Verified ROI:</strong> Drive higher engagement and convert leads into closed deals faster.</li>
    <li><strong>🔒 24/7 Reliability:</strong> Dedicated support and guaranteed performance.</li>
</ul>
<p><br></p>
<p>Use code <strong>SAVE20</strong> at checkout or click below to activate your exclusive pricing today.</p>
<p><br></p>
<p style="text-align: center;">
    <a href="https://callinggen.in/dashboard" target="_blank" style="background-color: #2563eb; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block;">{cta_text}</a>
</p>
<p><br></p>
<p>Warm regards,</p>
<p><strong>The {company_ref} Team</strong></p>"""

        elif "follow" in prompt_lower or "demo" in prompt_lower or "meeting" in prompt_lower:
            subject = f"Following up on our recent conversation, {{name}} | {company_ref}"
            heading = f"Next Steps Regarding Your Demo Consultation"
            cta_text = "Schedule a Quick Follow-Up &rarr;"
            body = f"""<h2><strong>Continuing Our Discussion on Automation</strong></h2>
<p><br></p>
<p>Hello <strong>{{{{name}}}}</strong>,</p>
<p><br></p>
<p>Thank you for taking the time to connect with us recently regarding <strong>{company_ref}</strong>. I wanted to follow up and see if you had any questions regarding how our conversational AI platform can streamline operations for <strong>{{{{company}}}}</strong>.</p>
<p><br></p>
<p><strong>Key Takeaways from Our Discussion:</strong></p>
<ul>
    <li><strong>Custom Voice Agents:</strong> Tailored voice personas in multiple regional languages.</li>
    <li><strong>Automated Workflows:</strong> Instant CRM lead capture and scheduled call automation.</li>
    <li><strong>Clear Scalability:</strong> Flexible pay-as-you-go credit packs with zero lock-in.</li>
</ul>
<p><br></p>
<p>Would you have 10 minutes this week for a brief call to review your customized implementation plan?</p>
<p><br></p>
<p style="text-align: center;">
    <a href="https://callinggen.in/calendar" target="_blank" style="background-color: #2563eb; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block;">{cta_text}</a>
</p>
<p><br></p>
<p>Best regards,</p>
<p><strong>The {company_ref} Team</strong></p>"""

        elif "launch" in prompt_lower or "announce" in prompt_lower or "feature" in prompt_lower:
            subject = f"🚀 Big News: Introducing Our Latest Features at {company_ref}"
            heading = f"Exciting New Capabilities Now Live at {company_ref}"
            cta_text = "See What's New &rarr;"
            body = f"""<h2><strong>🚀 Discover Our Latest Product Enhancements</strong></h2>
<p><br></p>
<p>Hello <strong>{{{{name}}}}</strong>,</p>
<p><br></p>
<p>We are thrilled to announce a major update at <strong>{company_ref}</strong>. Built directly from your feedback, these new features will help you automate communications faster and more effectively.</p>
<p><br></p>
<p><strong>What's New in This Release:</strong></p>
<ul>
    <li><strong>✨ Enhanced Performance:</strong> Sub-second response times and intelligent routing.</li>
    <li><strong>📊 Live Analytics:</strong> Real-time conversion tracking and detailed call summaries.</li>
    <li><strong>🌐 Custom Domains:</strong> Full white-label outbound email verification and branding.</li>
</ul>
<p><br></p>
<p>Log in to your workspace today to explore the new features.</p>
<p><br></p>
<p style="text-align: center;">
    <a href="https://callinggen.in/dashboard" target="_blank" style="background-color: #2563eb; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block;">{cta_text}</a>
</p>
<p><br></p>
<p>Warmly,</p>
<p><strong>The {company_ref} Product Team</strong></p>"""

        else:
            subject = f"Important Update from {company_ref} for {{{{name}}}}"
            heading = f"Unlocking Growth and Efficiency with {company_ref}"
            cta_text = "Learn More &amp; Get Started &rarr;"
            body = f"""<h2><strong>Empowering Your Workflow with {company_ref}</strong></h2>
<p><br></p>
<p>Hello <strong>{{{{name}}}}</strong>,</p>
<p><br></p>
<p>At <strong>{company_ref}</strong>, our mission is to empower your business with cutting-edge AI automation solutions that save time, reduce overhead, and accelerate growth for <strong>{{{{company}}}}</strong>.</p>
<p><br></p>
<p><strong>How We Help You Win:</strong></p>
<ul>
    <li><strong>Conversational Voice Agents:</strong> Handle inbound & outbound calls with natural tone and accuracy.</li>
    <li><strong>Email Broadcasts:</strong> Deliver targeted marketing campaigns with high inbox deliverability.</li>
    <li><strong>Enterprise Integration:</strong> Connect directly to your existing CRM and marketing pipelines.</li>
</ul>
<p><br></p>
<p>Ready to see how we can help your team achieve its goals this quarter?</p>
<p><br></p>
<p style="text-align: center;">
    <a href="https://callinggen.in" target="_blank" style="background-color: #2563eb; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block;">{cta_text}</a>
</p>
<p><br></p>
<p>Best regards,</p>
<p><strong>The {company_ref} Team</strong></p>"""

        return EmailAIGenerateResponse(
            subject=subject,
            heading=heading,
            body=body,
            cta_text=cta_text,
            cta_link="https://callinggen.in",
            tone=tone,
        )
