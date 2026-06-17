# SkyCargo OCR — Azure Document Intelligence

Simple Python tool that runs OCR on AWB PDFs using **Azure AI Document Intelligence** and produces a **JSON** result and a **Markdown** summary for each file.

## How it works

1. Drop one or more PDFs into the [input](input) folder.
2. Run the script.
3. For each `name.pdf`, you get `output/name.json` (structured result) and `output/name.md` (readable summary).

## Setup

```powershell
# from the skycargo-ocr folder
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# configure credentials
Copy-Item .env.example .env
# then edit .env and paste your Document Intelligence API key (or use az login)
```

### Authentication options

The endpoint is already set to the provided resource:

```
https://docintelligencmbc.cognitiveservices.azure.com/
```

Choose one:

- **API key** — paste it into `DOCUMENTINTELLIGENCE_API_KEY` in `.env`.
  Get it with:
  ```powershell
  az cognitiveservices account keys list `
    --name docintelligencmbc `
    --resource-group logicapp-rg
  ```
- **Azure AD** — leave the key blank and sign in with `az login`. Your identity
  needs the **Cognitive Services User** role on the resource.

## Run

```powershell
python main.py
```

Output:

```
Found 1 PDF(s). Using model 'prebuilt-layout'.
  -> Analyzing awb-sample.pdf ...
     JSON -> output\awb-sample.json
     MD   -> output\awb-sample.md
Done. 1 succeeded, 0 failed.
```

## Model selection

Set `DOCUMENTINTELLIGENCE_MODEL` in `.env`:

| Model | Use |
|---|---|
| `prebuilt-layout` (default) | Text + tables + structure — best for AWBs |
| `prebuilt-read` | Plain text / handwriting only, faster |

## Resource

- Resource: `docintelligencmbc`
- Resource group: `logicapp-rg`
- Subscription: `7d1e8453-2920-4f6d-9a6e-bc7005c10a22`
