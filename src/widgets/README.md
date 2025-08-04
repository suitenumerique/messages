# Widgets

This directory contains two separate Vite applications for different widgets:

## Widgets

### 1. Feedback Widget
- **Entry point**: `src/widgets/feedback/main.ts`
- **Output**: `dist/feedback.js`
- **Purpose**: A feedback form widget for collecting user feedback

### 2. Loader Widget
- **Entry point**: `src/widgets/loader/main.ts`
- **Output**: `dist/loader.js`
- **Purpose**: A loading spinner widget with toggle functionality

## Development

### Run both widgets in development mode:
```bash
npm run dev
```

### Run individual widgets:
```bash
# Feedback widget
npm run dev:feedback

# Loader widget
npm run dev:loader
```

## Building

### Build both widgets:
```bash
npm run build
```

### Build individual widgets:
```bash
# Build feedback widget
npm run build:feedback

# Build loader widget
npm run build:loader
```

## Preview

### Preview built widgets:
```bash
# Preview feedback widget
npm run preview:feedback

# Preview loader widget
npm run preview:loader
```

## Usage

### Feedback Widget
Include the feedback widget in your HTML:
```html
<div id="feedback-widget"></div>
<script src="dist/feedback.js"></script>
```

### Loader Widget
Include the loader widget in your HTML:
```html
<div id="loader-widget"></div>
<script src="dist/loader.js"></script>
```

**Note**: Each widget is bundled as a single JS file that includes its own CSS styles. No additional CSS files are required.

## File Structure

```
src/widgets/
├── src/
│   ├── widgets/
│   │   ├── feedback/
│   │   │   ├── main.ts          # Feedback widget entry point
│   │   │   └── styles.css       # Feedback widget styles
│   │   ├── loader/
│   │   │   ├── main.ts          # Loader widget entry point
│   │   │   └── styles.css       # Loader widget styles
│   └── main.ts                  # Original main entry (legacy)
├── feedback.html                 # Feedback widget test page
├── loader.html                   # Loader widget test page
├── vite.config.ts               # Vite configuration
└── package.json                 # Package configuration
``` 