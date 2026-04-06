import os
import google.generativeai as genai
from dotenv import load_dotenv
import re

def generate_dynamic_html():
    # Load environment variables
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not found in .env file.")
        return

    # Configure the Gemini API
    genai.configure(api_key=api_key)
    
    # 1. Ask the user for a topic in real-time
    print("Welcome to the Dynamic HTML Generator!")
    topic = input("Please enter a topic for your new HTML page: ").strip()
    
    if not topic:
        print("No topic provided. Exiting.")
        return
        
    print(f"\nGenerating a modern HTML page for '{topic}'... Please wait.")

    # 2. Build the prompt
    prompt = f"""
    You are an expert web developer and designer. 
    Create a complete, modern, responsive, and visually appealing HTML page about: "{topic}".
    
    Requirements:
    - Include embedded CSS for styling (make it look modern, clean, and professional).
    - Include a structured layout (header, main content area, footer).
    - Add placeholder images or colorful CSS shapes if needed.
    - Return ONLY the raw HTML code. Do NOT wrap it in ```html ... ``` markdown blocks.
    """

    try:
        # 3. Call the AI model
        model = genai.GenerativeModel("models/gemini-flash-latest")
        response = model.generate_content(prompt)
        
        # Clean up any potential markdown formatting from the response
        html_content = response.text.strip()
        if html_content.startswith("```html"):
            html_content = html_content[7:]
        if html_content.endswith("```"):
            html_content = html_content[:-3]
        html_content = html_content.strip()

        # 4. Save the generated HTML to a file
        # Create a safe filename based on the topic
        safe_filename = re.sub(r'[^a-z0-9]+', '_', topic.lower()).strip('_')
        if not safe_filename:
            safe_filename = "generated_page"
        filename = f"{safe_filename}.html"

        with open(filename, "w", encoding="utf-8") as f:
            f.write(html_content)

        print(f"\nSuccess! The HTML page has been generated and saved to: {filename}")
        print(f"You can now open {filename} in your browser to view it.")

    except Exception as e:
        print(f"\nAn error occurred while generating the page: {e}")

if __name__ == "__main__":
    generate_dynamic_html()
