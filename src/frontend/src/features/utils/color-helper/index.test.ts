import { ColorHelper } from './index';

describe('ColorHelper', () => {
  describe('getContrastColor', () => {
    describe('with default options', () => {
      it('should return dark color for light backgrounds', () => {
        expect(ColorHelper.getContrastColor('#FFFFFF')).toBe('#000000');
        expect(ColorHelper.getContrastColor('#F0F0F0')).toBe('#000000');
        expect(ColorHelper.getContrastColor('#E0E0E0')).toBe('#000000');
        expect(ColorHelper.getContrastColor('#D0D0D0')).toBe('#000000');
      });

      it('should return light color for dark backgrounds', () => {
        expect(ColorHelper.getContrastColor('#000000')).toBe('#FFFFFF');
        expect(ColorHelper.getContrastColor('#1A1A1A')).toBe('#FFFFFF');
        expect(ColorHelper.getContrastColor('#2D2D2D')).toBe('#FFFFFF');
        expect(ColorHelper.getContrastColor('#404040')).toBe('#FFFFFF');
      });

      it('should handle colors with # prefix', () => {
        expect(ColorHelper.getContrastColor('#FFFFFF')).toBe('#000000');
        expect(ColorHelper.getContrastColor('#000000')).toBe('#FFFFFF');
      });

      it('should handle colors without # prefix', () => {
        expect(ColorHelper.getContrastColor('FFFFFF')).toBe('#000000');
        expect(ColorHelper.getContrastColor('000000')).toBe('#FFFFFF');
      });
    });

    describe('with custom options', () => {
      it('should use custom light and dark colors', () => {
        const options = { lightColor: '#00FF00', darkColor: '#FF0000' };
        
        expect(ColorHelper.getContrastColor('#FFFFFF', options)).toBe('#FF0000');
        expect(ColorHelper.getContrastColor('#000000', options)).toBe('#00FF00');
      });

      it('should use custom light color only', () => {
        const options = { lightColor: '#00FF00' };
        
        expect(ColorHelper.getContrastColor('#FFFFFF', options)).toBe('#000000'); // default dark
        expect(ColorHelper.getContrastColor('#000000', options)).toBe('#00FF00'); // custom light
      });

      it('should use custom dark color only', () => {
        const options = { darkColor: '#FF0000' };
        
        expect(ColorHelper.getContrastColor('#FFFFFF', options)).toBe('#FF0000'); // custom dark
        expect(ColorHelper.getContrastColor('#000000', options)).toBe('#FFFFFF'); // default light
      });
    });

    describe('edge cases', () => {
      it('should handle gray colors around the threshold', () => {
        // Colors with luminance close to 0.5
        expect(ColorHelper.getContrastColor('#808080')).toBe('#000000');
        expect(ColorHelper.getContrastColor('#7F7F7F')).toBe('#FFFFFF');
        expect(ColorHelper.getContrastColor('#818181')).toBe('#000000');
      });

      it('should handle pure colors', () => {
        expect(ColorHelper.getContrastColor('#FF0000')).toBe('#FFFFFF');
        expect(ColorHelper.getContrastColor('#00FF00')).toBe('#000000');
        expect(ColorHelper.getContrastColor('#0000FF')).toBe('#FFFFFF');
      });

      it('should handle mixed colors', () => {
        expect(ColorHelper.getContrastColor('#FF8000')).toBe('#000000');
        expect(ColorHelper.getContrastColor('#8000FF')).toBe('#FFFFFF');
        expect(ColorHelper.getContrastColor('#00FFFF')).toBe('#000000');
      });
    });
  });
}); 
