{{- define "yourhealth.name" -}}{{ .Chart.Name }}{{- end -}}
{{- define "yourhealth.fullname" -}}{{ .Release.Name }}{{- end -}}

{{- define "yourhealth.selectorLabels" -}}
app.kubernetes.io/name: {{ include "yourhealth.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "yourhealth.labels" -}}
{{ include "yourhealth.selectorLabels" . }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | trunc 63 | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
