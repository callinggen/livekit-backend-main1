"""
CallingGen — Reusable Clean Semantic Marketing Templates.
Only the editable inner text/body content is stored here, making editing effortless in rich text editors.
The modern CallingGen branded wrapper (header, card frame, styles, and footer) is applied automatically during preview and delivery.
"""

DEFAULT_TEMPLATES = [
    # ── 1. COMPANY INTRODUCTION ──────────────────────────────────────────────────
    {
        "name": "Company Introduction",
        "category": "Business",
        "description": "Introduce your company's mission, key services, and value to prospective clients or new accounts.",
        "subject": "Introducing {{company}} — Solutions designed for your business",
        "preview_text": "Discover how our services and solutions help you achieve better results.",
        "html_body": """<h2><strong>Partnering with {{company}} for Your Growth</strong></h2>
<p><br></p>
<p>Hello <strong>{{name}}</strong>,</p>
<p><br></p>
<p>Thank you for connecting with <strong>{{company}}</strong>. We specialize in providing reliable, high-impact conversational AI and workflow automation solutions designed to simplify your operations, enhance customer satisfaction, and drive measurable outcomes for your business.</p>
<p><br></p>
<p><strong>How We Support You:</strong></p>
<ul>
    <li><strong>Tailored Solutions:</strong> Workflows and voice agents customized precisely to your business needs.</li>
    <li><strong>Dedicated Support:</strong> An experienced team committed to your success at every step.</li>
    <li><strong>Proven Results:</strong> Transparent metrics, fast turnaround times, and consistent quality.</li>
</ul>
<p><br></p>
<p>We would love to learn more about your current goals and share how we can collaborate.</p>
<p><br></p>
<p style="text-align: center;">
    <a href="#" target="_blank" style="background-color: #2563eb; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block;">Explore Our Services &rarr;</a>
</p>
<p><br></p>
<p>Best regards,</p>
<p><strong>The {{company}} Team</strong></p>""",
    },

    # ── 2. NEW PRODUCT LAUNCH ────────────────────────────────────────────────────
    {
        "name": "New Product Launch",
        "category": "Sales",
        "description": "Announce a new product, service feature, or major update to your customers and subscribers.",
        "subject": "🚀 Exciting News: Announcing our latest release at {{company}}",
        "preview_text": "Check out the newest additions and upgrades designed to give you an edge.",
        "html_body": """<h2><strong>🚀 Introducing Our Latest Release at {{company}}</strong></h2>
<p><br></p>
<p>Hello <strong>{{name}}</strong>,</p>
<p><br></p>
<p>We are thrilled to announce our latest release at <strong>{{company}}</strong>. Our new offering was built directly with your feedback to deliver faster performance, better efficiency, and greater value for your team.</p>
<p><br></p>
<p><strong>Key Highlights &amp; What's New:</strong></p>
<ul>
    <li><strong>✨ Smarter Capabilities:</strong> Enhanced AI tools designed to speed up your everyday workflow.</li>
    <li><strong>⚡ Streamlined Integration:</strong> Connect seamlessly with your existing processes with zero downtime.</li>
    <li><strong>📈 Immediate Value:</strong> Experience immediate improvements from day one with intuitive controls.</li>
</ul>
<p><br></p>
<p>Ready to see what is new and test out the new features?</p>
<p><br></p>
<p style="text-align: center;">
    <a href="#" target="_blank" style="background-color: #2563eb; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block;">Learn More &amp; Get Started &rarr;</a>
</p>
<p><br></p>
<p>Warm regards,</p>
<p><strong>The {{company}} Team</strong></p>""",
    },

    # ── 3. CUSTOMER SUPPORT ──────────────────────────────────────────────────────
    {
        "name": "Customer Support & Assistance",
        "category": "Support",
        "description": "Provide direct support channels, help resources, and customer care contact details to your clients.",
        "subject": "How can we assist you today, {{name}}? Support from {{company}}",
        "preview_text": "We are here to help you get the most out of your experience.",
        "html_body": """<h2><strong>We Are Here to Support You, {{name}}</strong></h2>
<p><br></p>
<p>Hello <strong>{{name}}</strong>,</p>
<p><br></p>
<p>At <strong>{{company}}</strong>, ensuring you have a seamless experience is our top priority. Whether you have questions, need assistance with your account, or want guidance on best practices, our support team is standing by to help.</p>
<p><br></p>
<p><strong>Ways to Reach Our Team:</strong></p>
<ul>
    <li><strong>Email Support:</strong> Reply directly to this email or reach out to our dedicated support desk.</li>
    <li><strong>Help Center &amp; Guides:</strong> Access step-by-step documentation, tutorials, and FAQs anytime.</li>
    <li><strong>Direct Assistance:</strong> Connect with your account representative for one-on-one onboarding.</li>
</ul>
<p><br></p>
<p>If there is anything you need or if you have any feedback, please feel free to reach out to us.</p>
<p><br></p>
<p style="text-align: center;">
    <a href="#" target="_blank" style="background-color: #0f172a; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block;">Contact Customer Support &rarr;</a>
</p>
<p><br></p>
<p>Warm regards,</p>
<p><strong>The {{company}} Support Team</strong></p>""",
    },

    # ── 4. LEAD FOLLOW-UP ────────────────────────────────────────────────────────
    {
        "name": "Lead Follow-Up",
        "category": "Sales",
        "description": "Follow up with warm prospects, demo attendees, or inquiries regarding your services.",
        "subject": "Following up on your inquiry with {{company}}, {{name}}",
        "preview_text": "Quick follow-up regarding our recent conversation and next steps.",
        "html_body": """<h2><strong>Next Steps Regarding Your Inquiry</strong></h2>
<p><br></p>
<p>Hello <strong>{{name}}</strong>,</p>
<p><br></p>
<p>I wanted to follow up on your recent inquiry with <strong>{{company}}</strong>. We understand that finding the right conversational AI and automation solution is essential for your team's success.</p>
<p><br></p>
<p><strong>How We Help Our Partners:</strong></p>
<ul>
    <li><strong>Comprehensive Onboarding:</strong> Tailored setups designed for your specific business workflow.</li>
    <li><strong>High Reliability:</strong> Industry-leading uptime, sub-second latency, and dedicated assistance.</li>
    <li><strong>Flexible Scalability:</strong> Plans designed to seamlessly scale with your organization's growth.</li>
</ul>
<p><br></p>
<p>Would you have a few minutes this week for a brief conversation to discuss how we can assist you?</p>
<p><br></p>
<p style="text-align: center;">
    <a href="#" target="_blank" style="background-color: #2563eb; color: #ffffff; padding: 12px 28px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block;">Schedule a Quick Call &rarr;</a>
</p>
<p><br></p>
<p>Best regards,</p>
<p><strong>The {{company}} Team</strong></p>""",
    }
]
