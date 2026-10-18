{{- define "yourhealth.selectorLabels" -}}
app.kubernetes.io/name: yourhealth
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
