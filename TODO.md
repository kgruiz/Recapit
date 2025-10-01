# TODO List

- Handle rate limit errors by waiting and retrying.
- Add support for other slide formats (e.g., German 322).
- Change the default output directory for transcribe image functions to a new directory in the same parent directory as input images.
- Add support for different models and automatically adjust rate limits.
- Include the function name in console error messages.
- Add configuration support for models. Example:
  ```python
  config = types.GenerateContentConfig(
          temperature=0.7,
          max_output_tokens=150
  )
  ```
- Specialize outputs for single items (e.g., include file path, name, plurals, etc.).
- Add an option to output as Markdown, either exclusively or in addition to LaTeX. Consider converting LaTeX to Markdown or modifying prompts to output Markdown natively.
- Create a function to determine the default output path for a given input path to simplify locating output files.
- Add a model parameter to all helper functions.
- Provide options to skip saving the full response and/or pickles.
- Ensure models used are not deprecated.
- Add a parameter to specify a different output file name.
- Add a recursive option to all applicable functions.
- Verify the type and value of all parameters, especially for rate limiters.
- Implement limits for TPM (tokens per minute) and RPD (requests per day).
- Refactor functions to use a code block removal utility.
- Handle multiple rate limits within a single run.
- Implement error handling to continue processing after exceptions. Examples:
  - Transcribing individual image files:
    ```
    ⠼ Transcribing individual image files 93% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╸━━━━━━━ 25/27 • 0:03:06 • 0:00:26
    ⠼ Transcribing setting-alarm.png 0% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 0/1 • 0:00:00 • -:--:--
    ```
    Error:
    ```
    google.genai.errors.ServerError: 503 UNAVAILABLE. {'error': {'code': 503, 'message': 'The service is currently unavailable.', 'status': 'UNAVAILABLE'}}
    ```
  - Transcribing slides:
    ```
    ⠧ Transcribing 465 Lecture 1.pdf 0% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 0/16 • 0:00:38 • -:--:--
    ⠧ Transcribing slides from 465 Lecture 1.pdf 5% ━━━━━━╸━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 14/272 • 0:00:38 • 0:11:13
    ```
    Error:
    ```
    requests.exceptions.ConnectionError: ('Connection aborted.', ConnectionResetError(54, 'Connection reset by peer'))
    ```
