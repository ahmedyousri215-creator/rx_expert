import os
import time
import urllib.parse
import requests
import re
import streamlit as st
from openai import OpenAI
import markdown
from html2image import Html2Image

# ==========================================
# 1. إعدادات الصفحة وواجهة المستخدم
# ==========================================
st.set_page_config(page_title="RX supplement EXPERT", page_icon="🧬", layout="wide")

# -- كود ضبط الاتجاه والتنسيق (RTL) المحدث --
st.markdown("""
<style>
    /* التطبيق بالكامل من اليمين لليسار */
    .stApp {
        direction: rtl;
    }
    
    /* إجبار النصوص العادية والجداول ومربعات الإدخال على اليمين (بدون التأثير على الحاويات الأساسية) */
    .stMarkdown p, .stMarkdown ul, .stMarkdown ol, .stMarkdown li, input, label, table, th, td {
        text-align: right !important;
        direction: rtl !important;
    }
    
    /* فئة مخصصة لعزل العنوان وإجباره على التمركز في المنتصف */
    .center-header {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center !important;
        direction: ltr !important;
        width: 100%;
    }
    
    /* ضبط شريط التابات (Tabs) */
    .stTabs [data-baseweb="tab-list"] {
        direction: rtl;
        justify-content: flex-start;
    }
    button[role="tab"] { direction: rtl; }
    
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# العناوين الرئيسية مجمعة في المنتصف باستخدام الفئة المخصصة
st.markdown("""
<div class="center-header">
    <h1 style="color: #2c3e50; margin-bottom: 0px;">🧬 RX supplement EXPERT</h1>
    <p style="font-size: 18px; color: #7f8c8d; margin-top: 5px; font-weight: bold;">CLAIMS VS FACTS</p>
</div>
""", unsafe_allow_html=True)

st.divider()

# العناوين الرئيسية (تأكد إن مفيش أي st.title أو st.markdown تانية للعناوين غير دول)
# العناوين الرئيسية مجمعة في المنتصف



# (ضع مفتاحك هنا)
api_key = st.secrets.get("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)

# إعداد ذاكرة التطبيق (عشان ما يعيدش التحميل مع كل سؤال)
if "audit_done" not in st.session_state:
    st.session_state.audit_done = False
if "studies_text" not in st.session_state:
    st.session_state.studies_text = ""
if "english_report" not in st.session_state:
    st.session_state.english_report = ""
if "arabic_infographic" not in st.session_state:
    st.session_state.arabic_infographic = ""
if "image_path" not in st.session_state:
    st.session_state.image_path = ""

def fetch_high_evidence_pubmed(ingredient: str, synonym: str):
    exact_ingredient = f'"{ingredient.strip()}"'
    if synonym:
        exact_synonym = f'"{synonym.strip()}"'
        refined_query = f"({exact_ingredient}[Title] OR {exact_synonym}[Title])"
    else:
        refined_query = f"{exact_ingredient}[Title]"
        
    raw_term = f'{refined_query} AND ("meta-analysis"[pt] OR "systematic review"[pt]) AND ("last 10 years"[dp])'
    encoded_term = urllib.parse.quote(raw_term)
    headers = {"User-Agent": "ClinicalAuditAgent/1.0"}
    
    search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={encoded_term}&retmode=json&retmax=50&sort=relevance"
    
    full_text_count = 0
    abstract_count = 0
    
    try:
        res = requests.get(search_url, headers=headers, timeout=15)
        res.raise_for_status()
        pmids = res.json().get("esearchresult", {}).get("idlist", [])
        
        if not pmids:
            return "ERROR: لم يتم العثور على أي أبحاث مطابقة في آخر 10 سنوات.", 0, 0
            
        summary_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id={','.join(pmids)}&retmode=json"
        summary_res = requests.get(summary_url, headers=headers, timeout=15)
        summaries = summary_res.json().get("result", {}) if summary_res.status_code == 200 else {}
        
        titles_list_str = ""
        for pmid in pmids:
            title = str(summaries.get(pmid, {}).get("title", "")).replace("&lt;", "<").replace("&gt;", ">")
            if title:
                titles_list_str += f"PMID: {pmid} | Title: {title}\n"
                
        status_text = st.empty()
        status_text.text("🧠 جاري فرز العناوين واستبعاد الحالات الحرجة والنادرة...")
        
        triage_prompt = f"""
        You are an expert OTC Clinical Pharmacist and Supplement Researcher.
        Below is a list of systematic reviews/meta-analyses for the ingredient: '{ingredient}'.
        
        Your task: Select ONLY the PMIDs of studies that are relevant to normal human supplementation, general wellness, preventative health, fitness, or common outpatient conditions.
        
        STRICTLY REJECT any study focusing on:
        - Acute hospital/ICU care (e.g., sepsis, surgery, burns, mechanical ventilation).
        - Rare genetic/metabolic syndromes or neonatal intensive care.
        - Oncology/cancer chemotherapy side-effects or dialysis.
        
        Return Maximum 15-20 best PMIDs as a comma-separated list of numbers only (e.g., 123456, 789012).
        
        Studies List:
        {titles_list_str}
        """
        
        # استدعاء الفلتر الذكي
        triage_res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": triage_prompt}],
            temperature=0.0
        )
        
        selected_pmids_raw = triage_res.choices[0].message.content.strip()
        filtered_pmids = [p.strip() for p in re.findall(r'\d+', selected_pmids_raw) if p.strip() in pmids]
        
        if not filtered_pmids:
            filtered_pmids = pmids[:15]
            
        compiled_studies = []
        progress_bar = st.progress(0)
        
        for i, pmid in enumerate(filtered_pmids):
            meta = summaries.get(pmid, {})
            title = str(meta.get("title", "No Title")).replace("&lt;", "<").replace("&gt;", ">")
            journal = meta.get("source", "Unknown Journal")
            year = meta.get("pubdate", "Unknown Year")
            link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            
            pmcid = None
            for aid in meta.get("articleids", []):
                if aid.get("idtype") == "pmc":
                    pmcid = aid.get("value")
                    break
            
            header_info = f"STUDY #{i+1}\nTitle: {title}\nJournal: {journal} | Year: {year}\nPMID: {pmid} | Direct Link: {link}\n"
            
            abs_text = ""
            try:
                abs_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&retmode=text&rettype=abstract"
                abs_res = requests.get(abs_url, headers=headers, timeout=12)
                if abs_res.status_code == 200: abs_text = abs_res.text.strip()
            except Exception:
                pass

            full_text_snippet = ""
            is_full_text = False
            if pmcid:
                try:
                    pmc_clean_id = pmcid.replace("PMC", "")
                    pmc_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={pmc_clean_id}&retmode=xml"
                    pmc_res = requests.get(pmc_url, headers=headers, timeout=15)
                    if pmc_res.status_code == 200 and "<body" in pmc_res.text:
                        body_match = re.search(r'<body.*?>(.*?)</body>', pmc_res.text, re.DOTALL)
                        raw_body = body_match.group(1) if body_match else pmc_res.text
                        
                        clean_body = re.sub(r'</?(sec|title|p|tr|td|th)[^>]*>', '\n', raw_body)
                        clean_body = re.sub(r'<[^>]+>', ' ', clean_body)
                        clean_body = re.sub(r'\s+', ' ', clean_body).strip()
                        
                        results_match = re.search(r'\b(3\.\s*Results|2\.\s*Results|Results and Discussion|Main Results|Subgroup Analysis|Discussion)\b', clean_body, re.IGNORECASE)
                        
                        if results_match and results_match.start() < len(clean_body) * 0.7:
                            start_idx = results_match.start()
                            extracted_core = clean_body[start_idx : start_idx + 20000]
                        else:
                            skip_intro = int(len(clean_body) * 0.20)
                            extracted_core = clean_body[skip_intro : skip_intro + 20000]
                            
                        full_text_snippet = f"\n[🌟 HIGH-PRIORITY FULL TEXT (RESULTS & DISCUSSION & DOSING DETAILS)]:\n{extracted_core}\n"
                        is_full_text = True
                except Exception:
                    pass
            
            if is_full_text:
                full_text_count += 1
                status_text.text(f"سحب النص الكامل ({i+1}/{len(filtered_pmids)}): {title[:40]}...")
            else:
                abstract_count += 1
                status_text.text(f"سحب الملخص ({i+1}/{len(filtered_pmids)}): {title[:40]}...")

            compiled_studies.append(f"{header_info}\n[ABSTRACT]:\n{abs_text}\n{full_text_snippet}\n")
            progress_bar.progress((i + 1) / len(filtered_pmids))
            time.sleep(0.3)
            
        stats_header = f"=== EXTRACTION STATISTICS ===\nTotal Studies Analyzed: {len(filtered_pmids)} (Filtered from {len(pmids)} candidates) | Full Text Extracted: {full_text_count} | Abstracts Only: {abstract_count}\n\n"
        status_text.text("✅ تم فرز وسحب الأبحاث الإكلينيكية بنجاح!")
        return stats_header + "".join(compiled_studies), full_text_count, abstract_count
    except Exception as e:
        return f"ERROR: {str(e)}", 0, 0
    
    

def generate_commercial_clinical_report(ingredient: str, studies_data: str, ft_count: int, abs_count: int) -> str:
    system_prompt = f"""
    You are a Senior Clinical Pharmacologist and Evidence-Based Medicine (EBM) Methodologist. 
    Your task is to author an authoritative, deeply analytical, and exhaustive **Clinical & Pharmacological Monograph** for ({ingredient}), grounded in the retrieved Systematic Reviews and Meta-Analyses ({ft_count} Deep Full-Text Excerpts, {abs_count} Abstracts).

    ### CORE ANALYTICAL MANDATES:
    1. **DEEP FULL-TEXT MINING (CRITICAL):** Studies marked with `[🌟 HIGH-PRIORITY FULL TEXT]` contain raw Results, Subgroup Analyses, and Discussion sections. You MUST aggressively mine these sections to extract:
       - *Subgroup nuances:* Why did trials succeed in specific cohorts and fail in others? (e.g., baseline deficiency vs. sufficiency, age, comorbidities, duration thresholds).
       - *Heterogeneity & Study Quality:* Extract $I^2$ values, confidence intervals (95% CI), p-values, and risk-of-bias notes whenever present.
       - *Mechanistic & Kinetic explanations:* How the authors explain null results, ceiling effects, or bioavailability limits.
    2. **STATISTICAL VS. CLINICAL SIGNIFICANCE:** Never blindly celebrate a p-value < 0.05. Explicitly judge whether the magnitude of effect (SMD, WMD, RR, OR, or absolute unit change) translates to meaningful real-world clinical benefit or trivial noise.
    3. **THE 4-GEAR RULE FOR EFFICACY:** Every clinical claim MUST specify: [Exact Clinical Indication] + [Chemical Form & Route] + [Dose Range & Duration Threshold] + [Exact Magnitude of Effect / Statistical Reality].
    4. **NO SHALLOW SUMMARIES:** Write with rigorous clinical depth. Explain the *Why* and *How* behind every conclusion.

    ---

    ### REQUIRED MONOGRAPH STRUCTURE:

    ## 1. BIOCHEMICAL & PHYSIOLOGICAL PROFILE (بروفايل المادة الفعالة)
    - **Endogenous vs. Essential Status:** Is ({ingredient}) synthesized endogenously by the human body (and under what rate-limiting pathways/organs), or is it strictly an essential exogenous nutrient/phytochemical?
    - **Core Physiological Architecture:** Its primary biochemical mechanisms, enzymatic co-factor roles, or receptor targets in human physiology.
    - **Epidemiology of Deficiency & Insufficiency:** How prevalent is clinical deficiency or subclinical insufficiency in the general population vs. specific high-risk subpopulations (based on the studies and established clinical knowledge)? What are the physiological consequences of depletion?
    - **Homeostasis & Saturation Kinetics:** Does the body tightly regulate its blood/tissue levels (e.g., saturable active transport, renal threshold/excretion of excess), or does it accumulate?

    ## 2. MACRO RESEARCH LANDSCAPE & TRAJECTORY (البانوراما البحثية وعلاقة الدراسات ببعضها)
    - **The Big Picture:** Synthesize how the retrieved systematic reviews relate to one another. Are researchers converging on a single primary clinical cluster (e.g., metabolic health, immunity), or is the research fragmented across disparate organ systems?
    - **Consensus vs. Controversy:** Identify where the meta-analyses show unified, reproducible agreement versus areas plagued by high heterogeneity ($I^2$) and conflicting outcomes.
    - **Evolution of Evidence:** Note if newer reviews or full-text subgroup analyses overturn older assumptions about ({ingredient}).

    ## 3. THE CORE EFFICACY MATRIX: CLAIMS, FACTS & WHY CLAIMS FAIL (مصفوفة الفعالية: الحقيقة مقابل الادعاء)
    *(This is the heart of the audit. Divide strictly into three analytical tiers and apply the 4-Gear Rule)*:
    - **Tier A — Proven Clinical Efficacy (أين تنجح المادة فعلياً وبقوة دليل عالية):** Detail the exact indications where ({ingredient}) produces both statistically and clinically meaningful outcomes. State exact effect sizes, doses, and required durations.
    - **Tier B — Conditional or Context-Dependent Efficacy (فعالية مشروطة بظروف محددة):** Indications where the ingredient ONLY works under strict conditions (e.g., *only* in baseline-deficient patients, *only* above a specific dose threshold, or *only* in high-stress/athletic/elderly cohorts).
    - **Tier C — Debunked, Overhyped, or Null Claims & THE ROOT CAUSE OF FAILURE (الادعاءات الفاشلة ولماذا لا تعمل):** List popular or tested uses where meta-analyses show null, negligible, or clinically meaningless effects. **Crucially, explain WHY these claims fail** based on the Full-Text Discussions (e.g., physiological ceiling effect in already-sufficient subjects, poor oral bioavailability, rapid renal clearance, short trial durations, or confounding variables).

    ## 4. CHEMICAL FORMS, BIOAVAILABILITY & SYNERGIES (الصيغ الكيميائية والامتصاص)
    - Compare all chemical forms, salts, derivatives, or delivery systems mentioned in the data (e.g., standard vs. chelated, active coenzyme forms, liposomal, sustained-release).
    - Detail absorption bottlenecks, saturable dose limits (how much can be absorbed in a single sitting), and clinically proven synergies or antagonists.

    ## 5. CLINICAL PROTOCOLS, TARGET DEMOGRAPHICS & SAFETY (البروتوكولات، الفئات المستهدفة، والأمان)
    - **Precision Dosing Matrix:** Breakdown of exact daily dosages, splitting schedules, and minimum timeline to clinical response per indication.
    - **Responder vs. Non-Responder Profile:** Who genuinely needs and benefits from supplementation vs. who will experience zero clinical return?
    - **Tolerability, Adverse Events & Contraindications:** Specific GI, renal, metabolic, or drug-interaction risks reported in the trials, along with Upper Tolerable Intake Levels (UL).

    ## 6. HIGH-EVIDENCE SOURCE REPOSITORY (فهرس المصادر الإكلينيكية)
    - List ALL analyzed studies cleanly at the very end, formatted with Study #, Exact Title, Journal, Year, Type ([Full-Text Analyzed] vs. [Abstract Analyzed]), and Direct PubMed Link.
    """

    user_prompt = f"""
    Execute an exhaustive, publication-grade Clinical & Pharmacological Monograph for: ({ingredient}).
    Strictly follow the 6-section architecture. Mine the [🌟 HIGH-PRIORITY FULL TEXT] sections deeply for subgroup analyses, $I^2$ heterogeneity, dosing thresholds, and the exact physiological/methodological reasons why certain claims succeed or fail.

    Retrieved Clinical Studies Data:
    {studies_data}
    """

    response = client.chat.completions.create(
        model="gpt-4o",
        temperature=0.1, # تقليل العشوائية لأقصى درجة لضمان الصرامة العلمية والأرقام الدقيقة
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    )
    return response.choices[0].message.content



def generate_arabic_infographic_report(english_report: str, ingredient: str, ft_count: int, abs_count: int) -> str:
    total_studies = ft_count + abs_count
    system_prompt = f"""
    أنت صيدلي إكلينيكي خبير ومسوق طبي B2C. حوّل التقرير السريري إلى دليل إنفوجرافيك عربي دسم يجيب على تساؤلات العميل بأرقام وتفاصيل دقيقة.
    القاعدة الذهبية (قانون التروس الأربعة): يُمنع كتابة جمل عائمة. اذكر (الصيغة وطريقة الاستخدام) + (الجرعة والمدة) + (حجم التأثير الحقيقي).
    الهيكل الإجباري:
    1. 🌟 **رسالة الاحتراف والثقة:** ابدأ بـ "أنت الآن بين يديك خلاصة {total_studies} مراجعة منهجية وتحليل تلوي ({ft_count} دراسة بالنص الكامل و {abs_count} بالملخصات)..."
    2. ⚖️ **تفنيد الادعاءات والحجم الحقيقي للتأثير:** جدول (ما أثبته العلم وحجم التأثير | ادعاءات غير مثبتة).
    3. 🧪 **الصيغة الكيميائية والتآزر الدوائي:** أفضل الصيغ وما يتآزر معها.
    4. ⏱️ **البروتوكول العملي الدقيق:** الجرعات والمدى الزمني للنتائج.
    5. 🎯 **الفئات المستهدفة بدقة:** (ينفعك جداً إذا كنت | لا ينفعك إذا كنت).
    6. 📚 **المراجع العلمية:** ضعها في ذيل الإنفوجراف في كود HTML هذا:
    <div style="font-size: 14px; direction: ltr; text-align: left; background-color: #e9ecef; padding: 15px; border-radius: 10px; margin-top: 30px; word-wrap: break-word;">
    <b>Scientific References:</b><br>
    - [Titles and Links]
    </div>
"""
    user_prompt = f"حوّل التقرير لمادة ({ingredient}) لإنفوجراف دسم:\n\n{english_report}"
    response = client.chat.completions.create(model="gpt-4o", temperature=0.15, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}])
    clean_output = re.sub(r'^```(markdown|html)?\n', '', response.choices[0].message.content)
    return re.sub(r'\n```$', '', clean_output).strip()

def answer_interactive_question(question: str, studies_context: str, report_context: str) -> str:
    system_prompt = "أنت مستشار طبي خبير. أجب بالتفصيل والأرقام والجرعات بناءً حصرياً على النصوص المرفقة. اذكر عنوان الدراسة للتوثيق."
    user_prompt = f"=== الأبحاث ===\n{studies_context}\n=== التقرير ===\n{report_context}\n\nالسؤال: {question}"
    response = client.chat.completions.create(model="gpt-4o", temperature=0.1, messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}])
    return response.choices[0].message.content

# ==========================================
# 3. بناء واجهة المستخدم (التطبيق)
# ==========================================


col1, col2 = st.columns(2)
with col1:
    ingredient = st.text_input("💊 أدخل المادة الفعالة (مثال: Zinc, Vitamin C):", value="")
with col2:
    synonym = st.text_input("🔬 مرادف علمي (اختياري):", value="")

if st.button("🚀 بدء التحليل الميداني العميق", use_container_width=True):
    if ingredient:
        st.session_state.audit_done = False
        with st.spinner('🔍 يتم الآن سحب البيانات من PubMed واستخراج النصوص...'):
            studies_text, ft_cnt, abs_cnt = fetch_high_evidence_pubmed(ingredient, synonym)
            
        if studies_text.startswith("ERROR:"):
            st.error(studies_text)
        else:
            st.success(f"📊 إحصائيات السحب: إجمالي ({ft_cnt + abs_cnt}) | نص كامل ({ft_cnt}) | ملخصات ({abs_cnt})")
            
            with st.spinner('🧠 يتم الآن كتابة التقرير الإنجليزي التفصيلي...'):
                english_report = generate_commercial_clinical_report(ingredient, studies_text, ft_cnt, abs_cnt)
                
            with st.spinner('🎨 يتم الآن هندسة الإنفوجراف العربي الشامل وتوليد الصورة...'):
                arabic_infographic = generate_arabic_infographic_report(english_report, ingredient, ft_cnt, abs_cnt)
                
                # توليد الصورة
                current_dir = os.getcwd()
                safe_name = ingredient.replace(' ', '_').replace('-', '_')
                image_name = f"{safe_name}_Infographic.png"
                image_path = os.path.join(current_dir, image_name)
                
                html_content = markdown.markdown(arabic_infographic, extensions=['tables'])
                css_style = """
                <style>
                    @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;700;900&display=swap');
                    body { font-family: 'Tajawal', sans-serif; direction: rtl; background-color: #f4f7f6; padding: 20px; color: #2c3e50; margin: 0; }
                    .container { background: #ffffff; padding: 35px; border-radius: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); border-top: 12px solid #0056b3; }
                    h1 { color: #0056b3; text-align: center; border-bottom: 4px solid #e0e0e0; padding-bottom: 20px; font-size: 36px; font-weight: 900; }
                    h2, h3 { color: #d35400; margin-top: 30px; font-size: 26px; font-weight: 800; border-right: 6px solid #0056b3; padding-right: 12px; }
                    table { width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 20px; }
                    th { background-color: #0056b3; color: #ffffff; padding: 14px; text-align: right; font-weight: 900; }
                    td { border: 1px solid #dddddd; padding: 14px; text-align: right; line-height: 1.7; vertical-align: top; }
                    tr:nth-child(even) { background-color: #f9f9f9; }
                    strong { color: #c0392b; font-weight: 900; }
                    ul, ol, p { font-size: 21px; line-height: 1.8; }
                </style>
                """
                full_html = f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="UTF-8">{css_style}</head><body><div class="container">{html_content}</div></body></html>"""
                hti = Html2Image(output_path=current_dir, size=(900, 3800))
                hti.screenshot(html_str=full_html, save_as=image_name)

            # حفظ البيانات في الذاكرة
            st.session_state.studies_text = studies_text
            st.session_state.english_report = english_report
            st.session_state.arabic_infographic = arabic_infographic
            st.session_state.image_path = image_path
            st.session_state.audit_done = True
    else:
        st.warning("يرجى إدخال اسم المادة الفعالة أولاً.")

# ==========================================
# 4. عرض النتائج وبوابة الأسئلة (لا تختفي بعد الضغط)
# ==========================================
if st.session_state.audit_done:
    st.divider()
    tab1, tab2, tab3 = st.tabs(["🖼️ الإنفوجراف العربي", "📑 التقرير الإنجليزي", "💬 بوابة الأسئلة الذكية"])
    
    with tab1:
        st.markdown(st.session_state.arabic_infographic, unsafe_allow_html=True)
        try:
            with open(st.session_state.image_path, "rb") as file:
                btn = st.download_button(
                    label="⬇️ تحميل الإنفوجراف كصورة (PNG)",
                    data=file,
                    file_name=os.path.basename(st.session_state.image_path),
                    mime="image/png",
                    use_container_width=True
                )
        except:
            st.info("جاري تجهيز الصورة...")
            
    with tab2:
        st.markdown(st.session_state.english_report)
        st.download_button("⬇️ تحميل التقرير الإنجليزي (Markdown)", data=st.session_state.english_report, file_name="Clinical_Audit_ENG.md", use_container_width=True)
        
    with tab3:
        st.markdown("### 💡 اسأل الأبحاث المستخرجة مباشرة:")
        user_q = st.text_input("اطرح أي سؤال تفصيلي أو استفسر عن جرعة معينة:")
        if st.button("أرسل السؤال"):
            if user_q:
                with st.spinner("جاري البحث في الـ 20 دراسة..."):
                    answer = answer_interactive_question(user_q, st.session_state.studies_text, st.session_state.english_report)
                    st.info(answer)