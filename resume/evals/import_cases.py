"""
Labelled cases for the import check (`manage.py jev_eval import`).

Each case is a source text as PDF extraction would give it, what the parser
returned, and which values are NOT supported by the source. Everything else is
expected to pass. Synthetic people only.
"""

CASES = [
    {
        "name": "english, fabricated metric and skill",
        "source": """
        Maya Chen | maya.chen@example.org | github.com/mayachen
        SKILLS  Python · FastAPI · Postgres · JS · Docker
        EXPERIENCE
        Backend Engineer — Northwind Logistics (Mar 2021 – Present)
        • Rewrote the order routing service in FastAPI
        • Moved nightly reports from cron scripts to Celery
        Junior Developer — Brightline Media (2019 – 2021)
        • Maintained the WordPress plugins for client sites
        EDUCATION
        Bogazici University — B.Sc. Computer Engineering, 2015–2019
        """,
        "extracted": {
            "user_info": {
                "full_name": "Maya Chen",
                "email": "maya.chen@example.org",
                "github": "https://github.com/mayachen",
                "skills": ["Python", "FastAPI", "PostgreSQL", "JavaScript", "Docker", "Kubernetes"],
            },
            "experience": [
                {
                    "title": "Backend Engineer", "company": "Northwind Logistics",
                    "start_date": "2021-03", "end_date": None, "current_role": True,
                    "description": [
                        "Rewrote the order routing service using FastAPI.",
                        "Migrated nightly reporting from cron scripts to Celery, cutting runtime by 60%.",
                    ],
                },
                {
                    "title": "Junior Developer", "company": "Brightline Media",
                    "start_date": "2019-01", "end_date": "2021-01", "current_role": False,
                    "description": ["Maintained WordPress plugins for client websites."],
                },
            ],
            "education": [
                {"school": "Bogazici University", "degree": "Bachelor",
                 "field_of_study": "Computer Engineering", "start_date": "2015-01", "end_date": "2019-01"}
            ],
        },
        "unsupported": {
            "Kubernetes",
            "Migrated nightly reporting from cron scripts to Celery, cutting runtime by 60%.",
        },
    },
    {
        "name": "turkish source, translated fields, forced degree",
        "source": """
        Deniz Arslan  deniz.arslan@example.com  +90 532 000 11 22
        YETENEKLER: Java, Spring Boot, Kafka
        İŞ DENEYİMİ
        Yazılım Mühendisi, Kuzey Bankası, 2020 - devam ediyor
        - Ödeme sisteminin Kafka ile entegrasyonunu yaptım
        - Kod incelemelerine katıldım
        EĞİTİM
        Ankara Fen Lisesi, 2011 - 2015
        """,
        "extracted": {
            "user_info": {
                "full_name": "Deniz Arslan",
                "email": "deniz.arslan@example.com",
                "phone": "+90 532 000 11 22",
                "skills": ["Java", "Spring Boot", "Kafka", "Microservices"],
            },
            "experience": [
                {
                    "title": "Software Engineer", "company": "Kuzey Bankası",
                    "start_date": "2020-01", "end_date": None, "current_role": True,
                    "description": [
                        "Integrated the payment system with Kafka.",
                        "Took part in code reviews.",
                        "Led a team of five engineers.",
                    ],
                }
            ],
            "education": [
                {"school": "Ankara Fen Lisesi", "degree": "Bachelor",
                 "field_of_study": "Science", "start_date": "2011-01", "end_date": "2015-01"}
            ],
        },
        "unsupported": {"Microservices", "Led a team of five engineers.", "Bachelor", "Science"},
    },
    {
        "name": "wrong dates and invented employer title",
        "source": """
        Sam Okafor — sam.okafor@example.net
        Data Analyst, Riverside Health, June 2018 – August 2020
        Built weekly dashboards in Tableau for the operations team.
        Freelance, 2020 – 2022: SQL reporting for small shops.
        """,
        "extracted": {
            "user_info": {"full_name": "Sam Okafor", "email": "sam.okafor@example.net", "skills": ["Tableau", "SQL"]},
            "experience": [
                {
                    "title": "Senior Data Scientist", "company": "Riverside Health",
                    "start_date": "2016-06", "end_date": "2020-08", "current_role": False,
                    "description": ["Built weekly Tableau dashboards for the operations team."],
                },
                {
                    "title": "Freelance Analyst", "company": "Freelance",
                    "start_date": "2020-01", "end_date": "2022-01", "current_role": False,
                    "description": ["SQL reporting for small shops."],
                },
            ],
        },
        # The source gives no title for the freelance work; "Analyst" is the
        # parser's own.
        "unsupported": {"Senior Data Scientist", "2016-06", "Freelance Analyst"},
    },
]
