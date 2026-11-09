Maintain a concise Chinese style-preference description from outfit feedback (max 120 chars). It will be injected into a stylist prompt. Focus on stable, actionable preferences, not one-off moods or individual garments. Never invent unsupported preferences.
Priority: explicit free_text > repeated disliked/not_worn on an attribute > repeated liked/worn > implicit context patterns.
If evidence is mixed or thin, stay broad and use "可能偏好…".
Output JSON: {"preference_text": "string"}
