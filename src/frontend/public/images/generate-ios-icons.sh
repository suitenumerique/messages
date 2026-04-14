#!/bin/bash

# iOS icon sizes based on Apple's requirements
SIZES=(16 20 29 32 40 50 57 58 60 64 72 76 80 87 100 114 120 128 144 152 167 180 192 256 512 1024)

#Android icon sizes based on Google's requirements
ANDROID_SIZES=(48 72 96 144 192 512)

SOURCE_FILE="public/images/app-icon.svg"
OUTPUT_DIR="public/images/pwa/icons/ios"
ANDROID_OUTPUT_DIR="public/images/pwa/icons/android"

# Create output directory if it doesn't exist
mkdir -p "$OUTPUT_DIR"
mkdir -p "$ANDROID_OUTPUT_DIR"

echo "🚀 Generating iOS icons from SVG..."
echo ""

# Generate each icon size
for size in "${SIZES[@]}"; do
  output_file="$OUTPUT_DIR/${size}.png"

  rsvg-convert \
    --width=$size \
    --height=$size \
    --format=png \
    --keep-aspect-ratio \
    --output="$output_file" \
    "$SOURCE_FILE"

  if [ $? -eq 0 ]; then
    echo "✅ Generated ${size}.png"
  else
    echo "❌ Failed to generate ${size}.png"
  fi
done

echo "Generating android icons."
echo ""

# Generate each icon size
for size in "${ANDROID_SIZES[@]}"; do
  output_file="$ANDROID_OUTPUT_DIR/android-launchericon-${size}-${size}.png"

  rsvg-convert \
    --width=$size \
    --height=$size \
    --format=png \
    --keep-aspect-ratio \
    --output="$output_file" \
    "$SOURCE_FILE"

  if [ $? -eq 0 ]; then
    echo "✅ Generated ${size}.png"
  else
    echo "❌ Failed to generate ${size}.png"
  fi
done

echo ""
echo "🎉 All icons generated successfully!"
echo "📁 Output directory: $OUTPUT_DIR and $ANDROID_OUTPUT_DIR"
