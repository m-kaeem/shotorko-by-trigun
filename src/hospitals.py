"""Public hospitals that treat dengue patients in each covered city.

Names come from DGHS announcements and news reports of where dengue patients
are admitted (DGHS dengue-dedicated hospital notices; DGHS/Civil Surgeon daily
admission reports quoted by BSS, The Daily Star, TBS, bdnews24, 2023-2026).
Coordinates are approximate (building-level accuracy is not needed for a
city-level readiness view). There is no open real-time bed-occupancy feed, so
the app shows forecast-based *expected pressure*, not live bed counts.
"""

HOSPITALS = [
    # Dhaka North
    {"name": "DNCC Dedicated Hospital (Mohakhali)", "city": "Dhaka North", "lat": 23.7790, "lon": 90.4045,
     "note": "Declared a dedicated dengue hospital by DGHS (800 beds)"},
    {"name": "Kurmitola General Hospital", "city": "Dhaka North", "lat": 23.8140, "lon": 90.4060,
     "note": "Runs dedicated dengue beds"},
    {"name": "Shaheed Suhrawardy Medical College Hospital", "city": "Dhaka North", "lat": 23.7700, "lon": 90.3710,
     "note": "Dengue ward for adults and children"},
    # Dhaka South
    {"name": "Dhaka Medical College Hospital", "city": "Dhaka South", "lat": 23.7255, "lon": 90.3976,
     "note": "Dedicated dengue wards; referral hub"},
    {"name": "Mugda Medical College Hospital", "city": "Dhaka South", "lat": 23.7300, "lon": 90.4320,
     "note": "Among the highest dengue admissions nationally"},
    {"name": "Sir Salimullah Medical College Mitford Hospital", "city": "Dhaka South", "lat": 23.7110, "lon": 90.4000,
     "note": "DGHS-listed dengue-dedicated; also receives Narayanganj patients"},
    # Gazipur
    {"name": "Shaheed Ahsan Ullah Master General Hospital (Tongi)", "city": "Gazipur", "lat": 23.8920, "lon": 90.4040,
     "note": "DGHS-listed dengue-dedicated hospital"},
    {"name": "Shaheed Tajuddin Ahmad Medical College Hospital", "city": "Gazipur", "lat": 23.9990, "lon": 90.4180,
     "note": "Main public hospital of Gazipur city"},
    # Narayanganj
    {"name": "Narayanganj 300-Bed Hospital (Khanpur)", "city": "Narayanganj", "lat": 23.6260, "lon": 90.5050,
     "note": "Main dengue admissions in Narayanganj city"},
    {"name": "Narayanganj General (Victoria) Hospital", "city": "Narayanganj", "lat": 23.6170, "lon": 90.5000,
     "note": "District general hospital"},
    # Chattogram
    {"name": "Chattogram Medical College Hospital", "city": "Chattogram", "lat": 22.3590, "lon": 91.8310,
     "note": "Largest dengue caseload in the division; dedicated dengue ward"},
    {"name": "Bangladesh Institute of Tropical & Infectious Diseases (Fouzdarhat)", "city": "Chattogram", "lat": 22.4200, "lon": 91.7570,
     "note": "Infectious-disease hospital admitting dengue patients"},
    {"name": "Chattogram General Hospital (Andarkilla, 250 beds)", "city": "Chattogram", "lat": 22.3380, "lon": 91.8350,
     "note": "Admits dengue patients daily"},
]

HOTLINE = {"number": "16263", "name": "Shastho Batayon (DGHS health line)"}
