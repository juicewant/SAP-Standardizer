import streamlit as st
import pandas as pd
import re
from nameparser import HumanName
import gender_guesser.detector as gender

# --- CONFIGURATION & CONSTANTS ---
class Config:
    # High Priority Separators
    PHONE_SEP = re.compile(r'[/;\\|+]|\b(LOC|EXT|LOCAL|EXTENSION|AND|OR|TO)\b', re.IGNORECASE)
    # Low Priority: Only split on hyphen if it has spaces or connects a range
    HYPHEN_RANGE_SEP = re.compile(r'\s+-\s+') 
    
    # CHANGED: Added '&' to allowed characters so P&G doesn't become PG
    COMPANY_CLEAN_REGEX = re.compile(r"[^a-zA-Z0-9\s.,:\'\-\(\)&]") 
    DIGITS_ONLY = re.compile(r"\D") 
    
    # CHANGED: Expanded to catch ATTY, ARCH, CPA, RN, MD and standard salutations
    TITLES_REMOVE = re.compile(r'\b(ENGR|ENG|RPH|RCH|R\.CH|PHD|DR|ATTY|ARCH|MR|MS|MRS|SIR|MAAM|CPA|MD|RN)\b\.?', re.IGNORECASE)
    # CHANGED: Added single quotes for nicknames e.g., 'Pit'
    NICKNAME_REMOVE = re.compile(r'\(.*?\)|".*?"|\'.*?\'') 
    
    MANUAL_GENDER_FIXES = {
        "FRITZIE": "Ms.", "MEANN": "Ms.", "LUFE": "Ms.", 
        "ARCHNIE": "Mr.", "PRINCESS": "Ms."
    }

# --- LOGIC ENGINE ---
class DataStandardizer:
    @st.cache_resource
    def get_gender_detector():
        return gender.Detector()

    @staticmethod
    def clean_company(name: str) -> str:
        if not name or not name.strip(): return ""
        # 1. Strip disallowed special characters
        name = Config.COMPANY_CLEAN_REGEX.sub("", name).title()
        
        # 2. Force uppercase on standard corporate suffixes and abbreviations
        abbrs = ['INC', 'CORP', 'OPC', 'PTE', 'LTD', 'LLC', 'CO', 'PH', 'PHILS']
        for abbr in abbrs:
            # Matches the exact word boundaries regardless of case, replaces with uppercase
            name = re.sub(rf'\b{abbr}\b', abbr, name, flags=re.IGNORECASE)
            
        return " ".join(name.split()).strip()

    @staticmethod
    def clean_individual(line: str):
        if not line or not line.strip():
            return {"Salutation": "", "First Name": "", "Last Name": ""}
        
        # 1. Strip any leading non-letter characters (e.g., ": Ramelito", "- Maria", "* John")
        text = re.sub(r'^[^a-zA-Z]+', '', line).strip()
        
        # 2. Remove titles and nicknames
        text = Config.NICKNAME_REMOVE.sub("", text)
        text = Config.TITLES_REMOVE.sub("", text)
        
        # 3. Clean punctuation
        text = text.replace(".", " ").replace(",", " ").upper()
        
        # 4. If multiple people are listed, isolate the first person
        for s in ['/', ' AND ', '&', '+', ' WITH ']:
            text = text.split(s)[0]
            
        # 5. Parse the name
        p = HumanName(text.strip())
        fn, ln, suffix = p.first, p.last, p.suffix
        
        # 6. Format Suffix and Last Name
        clean_suffix = re.sub(r'[^a-zA-Z]', '', suffix) if suffix else ""
        full_ln = f"{ln} {clean_suffix}".strip() if clean_suffix else ln
        
        if not fn and p.title: fn = p.title
        
        # 7. Final capitalization
        fn_clean = " ".join(fn.split()).strip().title()
        ln_clean = " ".join(full_ln.split()).strip().title()
        
        # Fix for 'Ma' abbreviation which gets stripped of its period earlier
        if fn_clean == "Ma":
            fn_clean = "Ma."
            
        # 8. Gender Detection
        detector = DataStandardizer.get_gender_detector()
        sal = ""
        if fn_clean:
            sal = "Mr." if "male" in detector.get_gender(fn_clean.split()[0]) else "Ms."
            if fn_clean.upper() in Config.MANUAL_GENDER_FIXES:
                sal = Config.MANUAL_GENDER_FIXES[fn_clean.upper()]
                
        return {
            "Salutation": sal, 
            "First Name": fn_clean, 
            "Last Name": ln_clean
        }

    @staticmethod
    def validate_phone(source_value: str):
        """Processes phone numbers: prioritizes Landline (10) over Mobile (11). Auto-fixes 02 9-digit numbers."""
        if source_value is None or str(source_value).strip() == "":
            return {"Standardized": "", "Remarks": "Empty Row/Cell"}
        
        # 1. Primary Split (Keywords and heavy symbols)
        initial_parts = Config.PHONE_SEP.split(str(source_value))
        
        # 2. Secondary Split (Hyphens with spaces only)
        final_parts = []
        for p in initial_parts:
            if p:
                final_parts.extend(Config.HYPHEN_RANGE_SEP.split(p))
        
        def _normalize(txt):
            clean = Config.DIGITS_ONLY.sub("", txt)
            if not clean: return ""
            if clean.startswith("63"):
                clean = "0" + clean[2:]
            if clean.startswith("00"): 
                clean = "0" + clean.lstrip("0")
            
            # --- AUTO-FIX FOR INSUFFICIENT LANDLINE ---
            if len(clean) == 9 and clean.startswith("02"):
                clean = clean[:2] + "8" + clean[2:]
                
            return clean

        candidates = [_normalize(p) for p in final_parts if _normalize(p)]
        
        if not candidates:
            return {"Standardized": "", "Remarks": "Invalid (No digits found)"}
        
        # --- PRIORITY SELECTION ---
        valid_mobile = [c for c in candidates if len(c) == 11 and c.startswith("0")]
        valid_landline = [c for c in candidates if len(c) == 10 and c.startswith("0")]
        
        if valid_landline:
            best_number = valid_landline[0]
            remark = "Valid 10 Digits (Fixed: Added 8)" if "8" in best_number[2:3] and any(len(Config.DIGITS_ONLY.sub("", p)) == 9 for p in final_parts) else "Valid 10 Digits (Province/Landline)"
        elif valid_mobile:
            best_number = valid_mobile[0]
            remark = "Valid 11 Digits (Mobile)"
        else:
            best_number = sorted(candidates, key=len, reverse=True)[0]
            length = len(best_number)
            if not best_number.startswith("0"):
                remark = "Invalid (Does not start with 0)"
            elif length >= 12:
                remark = f"Invalid (Exceeded digit {length})"
            else:
                remark = f"Invalid (Insufficient digit {length})"

        # Standardized Formatting
        length = len(best_number)
        if length >= 11 or best_number.startswith("09"):
            fmt = f"{best_number[0:4]} {best_number[4:7]} {best_number[7:]}" if length > 7 else best_number
        elif length >= 10:
            fmt = f"{best_number[0:3]} {best_number[3:6]} {best_number[6:]}"
        else:
            fmt = best_number

        return {"Standardized": fmt, "Remarks": remark}

# --- UI INTERFACE ---
class StreamlitApp:
    def __init__(self):
        st.set_page_config(page_title="Data Standardizer", layout="wide")
        self.apply_styles()

    @staticmethod
    def apply_styles():
        st.markdown("""
            <style>
            .stTabs [data-baseweb="tab-list"] { gap: 40px; justify-content: center; }
            div.stButton > button { width: 100%; border-radius: 4px; }
            .centered-text { text-align: center; }
            </style>
            """, unsafe_allow_html=True)

    def render(self):
        left, center, right = st.columns([1, 2, 1])
        with center:
            st.markdown("<h1 class='centered-text'>Data Standardizer</h1>", unsafe_allow_html=True)
            st.markdown("<p class='centered-text'>Governance Tool v3.0 - Full Suite Optimization</p>", unsafe_allow_html=True)

            tabs = st.tabs(["Company", "Individual", "Phone"])
            
            with tabs[0]:
                raw = st.text_area("Input Companies", height=200, key="c_in")
                if st.button("Process Companies"):
                    lines = raw.split('\n')
                    st.session_state.df_out = pd.DataFrame([
                        {"Original": l, "Standardized": DataStandardizer.clean_company(l)} 
                        for l in lines
                    ])

            with tabs[1]:
                raw = st.text_area("Input Names", height=200, key="i_in")
                if st.button("Process Individuals"):
                    lines = raw.split('\n')
                    st.session_state.df_out = pd.DataFrame([
                        DataStandardizer.clean_individual(l) for l in lines
                    ])

            with tabs[2]:
                raw = st.text_area("Input Phones", height=200, key="p_in")
                if st.button("Process Phones"):
                    lines = raw.split('\n')
                    results = []
                    for l in lines:
                        val = DataStandardizer.validate_phone(l)
                        results.append({
                            "Original": l,
                            "Standardized": val["Standardized"],
                            "Remarks": val["Remarks"]
                        })
                    st.session_state.df_out = pd.DataFrame(results)

            self.render_results()

    def render_results(self):
        if st.session_state.get('df_out') is not None:
            st.divider()
            df = st.session_state.df_out
            if "Remarks" in df.columns:
                valid_count = len(df[df['Remarks'].str.contains("Valid", na=False)])
                invalid_count = len(df) - valid_count
                
                col1, col2 = st.columns(2)
                col1.metric("Valid Entries", valid_count)
                col2.metric("Invalid Entries", invalid_count)
            
            st.dataframe(df, use_container_width=True)
            
            csv = df.to_csv(index=False)
            st.download_button("Download CSV", csv, "standardized_data.csv", "text/csv")
            
            if st.button("Reset"):
                st.session_state.df_out = None
                st.rerun()

if __name__ == "__main__":
    app = StreamlitApp()
    app.render()