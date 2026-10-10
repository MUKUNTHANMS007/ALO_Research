@echo off
REM ALO: finish data collection, then retrain. Safe to stop (Ctrl+C) and re-run: collection resumes.
REM Keep the laptop plugged in, awake and online. Log: collected\collect_log2.txt
cd /d "%~dp0"
set PYTHONUNBUFFERED=1

echo [1/4] Remaining variants (no_third_party_scripts, no_fonts, no_media), repeat 1, 1x CPU, cold cache
python -I collect.py --variants no_third_party_scripts,no_fonts,no_media --cpus cpu_1x --caches cold --repeats 1 >> collected\collect_log2.txt 2>&1

echo [2/4] Baseline repeat 2 (more data per site)
python -I collect.py --variants baseline --cpus cpu_1x --caches cold --repeats 2 >> collected\collect_log2.txt 2>&1

echo [3/4] Evaluate on the new data
python -I train_new.py > collected\train_new_output.txt 2>&1

echo [4/4] Export the model (back up the current one first)
if not exist model\v2_backup mkdir model\v2_backup
copy /Y model\alo_tree.joblib model\v2_backup\ >nul
copy /Y model\url_profiles.json model\v2_backup\ >nul
python -I train_model.py > collected\train_model_output.txt 2>&1

echo DONE. See collected\train_new_output.txt and collected\train_model_output.txt
pause
