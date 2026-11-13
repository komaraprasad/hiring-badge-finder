"""Built-in suggestion lists for the typeahead boxes. Free text is always allowed."""

# Roles that usually carry the #HIRING frame: the people who hire, not the candidates.
# Search for these and put the skill being hired for (e.g. Java) in "Hiring for".
# Order matters: the first entries are shown when the box is empty.
TITLES = [
    # recruiters
    "Technical Recruiter", "Recruiter", "Senior Recruiter", "Talent Acquisition Specialist",
    "Talent Acquisition Partner", "Talent Acquisition Lead", "Talent Acquisition Manager",
    "Recruitment Consultant", "Head of Talent",
    # hiring managers
    "Engineering Manager", "Senior Engineering Manager", "Tech Lead", "Team Lead",
    "Director of Engineering", "Head of Engineering", "VP of Engineering", "Software Engineering Manager",
    "QA Manager", "Test Manager", "Data Engineering Manager", "Data Science Manager", "Head of Data",
    "DevOps Manager", "Delivery Manager", "Product Manager", "Head of Product", "Design Manager",
    "Hiring Manager",
    # leaders and HR
    "Founder", "Co-Founder", "CEO", "CTO", "HR Manager", "HR Business Partner", "Head of HR",
]

# One-click fills for the Job titles box (at most 5 each, the per-search limit).
TITLE_PRESETS = {
    "Recruiters": ["Technical Recruiter", "Recruiter", "Talent Acquisition Specialist",
                   "Talent Acquisition Partner", "Talent Acquisition Manager"],
    # 2 titles: with 5 manager titles LinkedIn served its older page (no "Actively hiring" filter,
    # results only in a visible tab). "Engineering Manager" alone used the current page.
    "Tech hiring managers": ["Engineering Manager", "Senior Engineering Manager"],
    "Founders & CTOs": ["Founder", "Co-Founder", "CEO", "CTO", "HR Manager"],
}

LOCATIONS = [
    # India
    "Bengaluru, Karnataka, India", "Mumbai, Maharashtra, India", "Delhi, India",
    "New Delhi, Delhi, India", "Gurugram, Haryana, India", "Noida, Uttar Pradesh, India",
    "Hyderabad, Telangana, India", "Pune, Maharashtra, India", "Chennai, Tamil Nadu, India",
    "Kolkata, West Bengal, India", "Ahmedabad, Gujarat, India", "Jaipur, Rajasthan, India",
    "Kochi, Kerala, India", "Visakhapatnam, Andhra Pradesh, India", "Vijayawada, Andhra Pradesh, India",
    "Coimbatore, Tamil Nadu, India", "Indore, Madhya Pradesh, India", "Chandigarh, India",
    "Karnataka, India", "Maharashtra, India", "Telangana, India", "Tamil Nadu, India",
    "Andhra Pradesh, India", "Haryana, India", "Uttar Pradesh, India", "Gujarat, India",
    "India",
    # Rest of world
    "United States", "San Francisco Bay Area", "New York, New York, United States",
    "Seattle, Washington, United States", "Austin, Texas, United States",
    "Boston, Massachusetts, United States", "Chicago, Illinois, United States",
    "Toronto, Ontario, Canada", "Vancouver, British Columbia, Canada", "Canada",
    "London, England, United Kingdom", "Manchester, England, United Kingdom", "United Kingdom",
    "Dublin, Ireland", "Berlin, Germany", "Munich, Germany", "Amsterdam, Netherlands",
    "Paris, France", "Madrid, Spain", "Zurich, Switzerland", "Stockholm, Sweden",
    "Dubai, United Arab Emirates", "Abu Dhabi, United Arab Emirates", "Riyadh, Saudi Arabia",
    "Singapore", "Hong Kong SAR", "Tokyo, Japan", "Sydney, New South Wales, Australia",
    "Melbourne, Victoria, Australia", "Australia", "Remote",
]

# Suggestions for "Hiring for" (typing shows matches; any other skill can still be typed in full).
SKILLS = [
    "Java", "Spring Boot", "Spring", "Hibernate", "Microservices", "Python", "Django", "Flask", "FastAPI",
    "JavaScript", "TypeScript", "React", "React Native", "Angular", "Vue.js", "Next.js", "Node.js", "Express.js",
    "HTML", "CSS", "Go", "Golang", "Rust", "C", "C++", "C#", ".NET", "ASP.NET", "Kotlin", "Swift", "Flutter",
    "Dart", "Android", "iOS", "PHP", "Laravel", "Ruby", "Ruby on Rails", "Scala", "Elixir",
    "SQL", "MySQL", "PostgreSQL", "MongoDB", "Redis", "Oracle", "Cassandra", "Elasticsearch", "Kafka",
    "Spark", "Hadoop", "Airflow", "Snowflake", "Databricks", "Power BI", "Tableau", "ETL",
    "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Terraform", "Ansible", "Jenkins", "CI/CD", "DevOps",
    "SRE", "Linux", "Networking", "Cybersecurity", "Salesforce", "SAP", "ServiceNow",
    "Machine Learning", "Deep Learning", "Data Science", "Data Engineering", "Data Analyst", "AI",
    "Generative AI", "LLM", "NLP", "Computer Vision", "TensorFlow", "PyTorch", "MLOps",
    "QA", "SDET", "Selenium", "Cypress", "Playwright", "Appium", "Automation Testing", "Manual Testing",
    "Performance Testing", "JMeter", "API Testing", "Postman",
    "Full Stack", "Frontend", "Backend", "Mobile", "Embedded", "Blockchain", "UI/UX", "Figma",
    "Product Management", "Project Management", "Scrum", "Agile", "Business Analyst",
]
