def generate_risk_label(risk_flags):
    if "unlimited_approval" in risk_flags:
        return "🔴 High Risk"
    elif len(risk_flags) > 0:
        return "🟡 Medium Risk"
    else:
        return "🟢 Low Risk"
    